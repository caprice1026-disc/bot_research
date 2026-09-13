from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.config import load_research_config
from hyperliquid_ai_trader.research.data import (
    CommonCandleFeatures,
    RESEARCH_DECISION_INTERVAL_MS,
)
from hyperliquid_ai_trader.research.points import (
    PointCandidate,
    select_research_points,
    write_point_selection_jsonl,
)
from hyperliquid_ai_trader.research.responses import (
    ModelResponseRecord,
    PreparedRequestRecord,
    ResearchResponseError,
    read_prepared_requests_jsonl,
    validate_model_responses,
)


def _candidate(index: int) -> PointCandidate:
    decision_time_ms = index * RESEARCH_DECISION_INTERVAL_MS
    return PointCandidate(
        decision_time_ms=decision_time_ms,
        features=CommonCandleFeatures(
            feature_set="common_candles_v1",
            as_of_ms=decision_time_ms,
            return_1m=0.001,
            return_5m=0.002,
            return_15m=0.003,
            return_60m=0.004,
            realized_vol_5m=0.005,
            realized_vol_30m=0.006,
            atr_pct=0.07,
            volume_zscore=0.08,
        ),
    )


def _function_call() -> dict[str, object]:
    return {
        "name": "open_position",
        "args": {
            "side": "long",
            "stop_loss_pct": 0.3,
            "take_profit_pct": 0.6,
            "confidence": 0.55,
            "thesis": "fixture",
            "would_abstain": False,
            "abstain_reason": None,
        },
    }


def test_validate_responses_cli_accepts_only_matching_bounded_responses(tmp_path, capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / "configs" / "research" / "development.json"
    points_path = tmp_path / "points.jsonl"
    write_point_selection_jsonl(
        points_path,
        select_research_points([_candidate(1), _candidate(2)], count=2, seed=42),
    )
    requests_path = tmp_path / "requests.jsonl"
    assert cli.main(
        [
            "prepare-requests",
            "--config",
            str(config),
            "--points",
            str(points_path),
            "--output",
            str(requests_path),
        ]
    ) == 0
    capsys.readouterr()
    prepared_rows = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    responses_path = tmp_path / "responses.jsonl"
    responses_path.write_text(
        "\n".join(
            json.dumps(
                {
                    "request_id": row["request_id"],
                    "returned_model": row["requested_model"],
                    "received_at_ms": row["decision_time_ms"] + 1000,
                    "function_calls": [_function_call()],
                },
                sort_keys=True,
            )
            for row in prepared_rows
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "decisions.jsonl"

    assert cli.main(
        [
            "validate-responses",
            "--config",
            str(config),
            "--requests",
            str(requests_path),
            "--responses",
            str(responses_path),
            "--output",
            str(output),
        ]
    ) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert {row["request_id"] for row in rows} == {row["request_id"] for row in prepared_rows}
    assert all(row["decision"]["stop_loss_pct"] == "0.3" for row in rows)
    assert all(row["returned_model"] == "gemini-2.5-flash-lite" for row in rows)
    assert manifest["decision_count"] == 2
    assert len(manifest["source_requests_sha256"]) == 64
    assert json.loads(capsys.readouterr().out) == {
        "decision_count": 2,
        "decisions_path": str(output),
        "manifest_path": str(output.with_suffix(".jsonl.manifest.json")),
        "status": "ok",
    }


def test_prepared_request_reader_rejects_a_tampered_canonical_hash(tmp_path) -> None:
    path = tmp_path / "tampered.jsonl"
    path.write_text(
        json.dumps(
            {
                "decision_time_ms": RESEARCH_DECISION_INTERVAL_MS,
                "request_id": "trial:0" * 32,
                "trial_id": "trial",
                "request_hash": "0" * 64,
                "requested_model": "gemini-2.5-flash-lite",
                "canonical_payload": "{}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ResearchResponseError, match="invalid prepared request"):
        read_prepared_requests_jsonl(path)


def test_response_validation_rejects_a_different_returned_model() -> None:
    root = Path(__file__).resolve().parents[1]
    canonical_payload = json.dumps(
        {
            "feature_set": "common_candles_v1",
            "input_data": {"market": {"as_of_ms": RESEARCH_DECISION_INTERVAL_MS}},
            "requested_model": "gemini-2.5-flash-lite",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    request = PreparedRequestRecord(
        decision_time_ms=RESEARCH_DECISION_INTERVAL_MS,
        request_id=f"trial:{request_hash}",
        trial_id="trial",
        request_hash=request_hash,
        requested_model="gemini-2.5-flash-lite",
        canonical_payload=canonical_payload,
    )
    response = ModelResponseRecord(
        request_id=request.request_id,
        returned_model="gemini-3.5-flash-lite",
        received_at_ms=RESEARCH_DECISION_INTERVAL_MS,
        function_calls=(),
    )

    with pytest.raises(ResearchResponseError, match="returned model"):
        validate_model_responses(
            config=load_research_config(root / "configs" / "research" / "development.json"),
            requests=[request],
            responses=[response],
        )
