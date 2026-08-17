from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator

import requests

from base_lp.data.events import SWAP_TOPIC
from base_lp.data.rpc import normalize_address
from base_lp.schemas import LogRecord


class DuneError(RuntimeError):
    """Raised when Dune cannot execute or return a complete query result."""


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_literal(value: str) -> str:
    return _parse_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def build_swap_logs_sql(pool_address: str, start_utc: str, end_utc: str) -> str:
    """Build a bounded raw-log query that is compatible with the local decoder."""

    pool_address = normalize_address(pool_address)
    start = _parse_utc(start_utc)
    end = _parse_utc(end_utc)
    if start >= end:
        raise ValueError("start_utc must be before end_utc")
    return f"""
SELECT
    block_time,
    block_number,
    block_hash,
    contract_address,
    topic0,
    topic1,
    topic2,
    topic3,
    data,
    tx_hash,
    "index" AS log_index,
    tx_index
FROM base.logs
WHERE contract_address = {pool_address}
  AND topic0 = {SWAP_TOPIC}
  AND block_time >= TIMESTAMP '{_timestamp_literal(start_utc)}'
  AND block_time < TIMESTAMP '{_timestamp_literal(end_utc)}'
ORDER BY block_number, tx_index, "index"
""".strip()


def _dune_timestamp(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        raise ValueError("Dune block_time must be an ISO timestamp string")
    normalized = value.replace(" UTC", "+00:00").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def dune_row_to_log_record(row: dict[str, object]) -> LogRecord:
    topics: list[str] = []
    for name in ("topic0", "topic1", "topic2", "topic3"):
        value = row.get(name)
        if value is None:
            break
        topics.append(str(value).lower())
    if not topics:
        raise ValueError("Dune row is missing topic0")
    return LogRecord(
        block_number=int(row["block_number"]),
        transaction_index=int(row["tx_index"]),
        log_index=int(row["log_index"]),
        block_hash=str(row["block_hash"]).lower(),
        transaction_hash=str(row["tx_hash"]).lower(),
        address=normalize_address(str(row["contract_address"])),
        topics=topics,
        data=str(row["data"]).lower(),
        timestamp=_dune_timestamp(row["block_time"]),
    )


class DuneClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        base_url: str = "https://api.dune.com/api/v1",
    ) -> None:
        if not api_key.strip():
            raise ValueError("Dune API key must not be empty")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Dune-API-Key": self.api_key}

    def execute_sql(self, sql: str) -> str:
        response = self.session.post(
            f"{self.base_url}/sql/execute",
            json={"sql": sql},
            headers=self._headers,
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DuneError(f"Dune SQL execution request failed: {response.text}") from exc
        payload = response.json()
        execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
        if not isinstance(execution_id, str) or not execution_id:
            raise DuneError(f"Dune SQL execution did not return an execution_id: {payload}")
        return execution_id

    def wait_for_completion(
        self,
        execution_id: str,
        *,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        if poll_interval_seconds < 0 or timeout_seconds <= 0:
            raise ValueError("poll interval must be non-negative and timeout must be positive")
        deadline = time.monotonic() + timeout_seconds
        while True:
            response = self.session.get(
                f"{self.base_url}/execution/{execution_id}/status",
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise DuneError(f"Dune execution status request failed: {response.text}") from exc
            payload = response.json()
            state = payload.get("state") if isinstance(payload, dict) else None
            if state == "QUERY_STATE_COMPLETED":
                return payload
            if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
                raise DuneError(f"Dune execution {execution_id} ended with {state}: {payload}")
            if time.monotonic() >= deadline:
                raise DuneError(f"timed out waiting for Dune execution {execution_id}: {payload}")
            if poll_interval_seconds:
                time.sleep(poll_interval_seconds)

    def iter_result_rows(
        self,
        execution_id: str,
        *,
        page_size: int = 10_000,
        columns: list[str] | None = None,
    ) -> Iterator[dict[str, object]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        offset = 0
        total_rows: int | None = None
        while total_rows is None or offset < total_rows:
            params: dict[str, object] = {"offset": offset, "limit": page_size}
            if columns:
                params["columns"] = ",".join(columns)
            response = self.session.get(
                f"{self.base_url}/execution/{execution_id}/results",
                headers=self._headers,
                params=params,
                timeout=self.timeout_seconds,
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise DuneError(f"Dune execution result request failed: {response.text}") from exc
            payload = response.json()
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise DuneError(f"Dune execution {execution_id} returned no result payload: {payload}")
            rows = result.get("rows")
            metadata = result.get("metadata")
            if not isinstance(rows, list) or not isinstance(metadata, dict):
                raise DuneError(f"Dune execution {execution_id} returned malformed rows: {payload}")
            total_rows = int(metadata.get("total_row_count", len(rows)))
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    raise DuneError("Dune result row must be an object")
                yield row
            offset += len(rows)
            if len(rows) < page_size:
                break
