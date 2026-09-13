"""Provider-neutral, resumable Batch submission state machine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from .config import ResearchConfig, ResearchConfigError, require_paid_api_permission
from .request_identity import PreparedModelRequest
from .store import ResearchStore, ResearchStoreError


class BatchError(ValueError):
    """Raised when a Batch cannot be submitted or resumed safely."""


class BatchProvider(Protocol):
    def submit(self, payloads: list[str]) -> str: ...


@dataclass(frozen=True)
class BatchResult:
    status: str
    submitted: int
    reused: int
    unknown: int


class BatchManager:
    reserved_cost_per_output_token = Decimal("0.000001")

    def submit(
        self,
        *,
        config: ResearchConfig,
        requests: list[PreparedModelRequest],
        store: ResearchStore,
        provider: BatchProvider,
        now_ms: int,
        max_output_tokens: int = 500,
    ) -> BatchResult:
        try:
            require_paid_api_permission(config)
        except ResearchConfigError as error:
            raise BatchError(str(error)) from error
        if not requests:
            raise BatchError("at least one request is required")
        if max_output_tokens <= 0:
            raise BatchError("max_output_tokens must be positive")
        reservation = self.reserved_cost_per_output_token * max_output_tokens * len(requests)
        if store.reserved_cost_total(config.experiment_id) + reservation > config.budget_usd:
            raise BatchError("reserved cost exceeds budget")
        fresh: list[PreparedModelRequest] = []
        reused = 0
        for request in requests:
            try:
                completed = store.completed_request_by_hash(config.experiment_id, request.request_hash)
            except ResearchStoreError:
                completed = None
            if completed is not None:
                reused += 1
                continue
            try:
                store.prepare_model_request(
                    experiment_id=config.experiment_id,
                    request_id=request.request_id,
                    trial_id=request.trial_id,
                    request_hash=request.request_hash,
                    canonical_payload=request.canonical_payload,
                    reserved_cost_usd=self.reserved_cost_per_output_token * max_output_tokens,
                    created_at_ms=now_ms,
                )
            except ResearchStoreError as error:
                raise BatchError("request reservation already exists") from error
            fresh.append(request)
        if not fresh:
            return BatchResult("reused", 0, reused, 0)
        try:
            job_id = provider.submit([request.canonical_payload for request in fresh])
        except TimeoutError:
            for request in fresh:
                store.mark_submission_unknown(request.request_id)
            return BatchResult("submission_unknown", 0, reused, len(fresh))
        except Exception as error:
            for request in fresh:
                store.mark_failed(request.request_id, error_type=type(error).__name__)
            raise BatchError("Batch submission failed") from error
        for request in fresh:
            store.mark_submitted(request.request_id, provider_job_id=job_id, submitted_at_ms=now_ms)
        return BatchResult("submitted", len(fresh), reused, 0)
