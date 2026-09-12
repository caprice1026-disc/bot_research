from __future__ import annotations

import json
from pathlib import Path

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.data import (
    CommonCandleFeatures,
    RESEARCH_DECISION_INTERVAL_MS,
)
from hyperliquid_ai_trader.research.points import (
    PointCandidate,
    select_research_points,
    write_point_selection_jsonl,
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


def test_prepare_requests_cli_writes_hashed_non_submitting_inputs(tmp_path, capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    points_path = tmp_path / "points.jsonl"
    write_point_selection_jsonl(
        points_path,
        select_research_points([_candidate(1), _candidate(2)], count=2, seed=42),
    )
    output = tmp_path / "requests.jsonl"

    assert cli.main(
        [
            "prepare-requests",
            "--config",
            str(root / "configs" / "research" / "development.json"),
            "--points",
            str(points_path),
            "--trial-prefix",
            "pilot-v001",
            "--output",
            str(output),
        ]
    ) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8"))
    first_payload = json.loads(rows[0]["canonical_payload"])

    assert len(rows) == 2
    assert len({row["request_id"] for row in rows}) == 2
    assert {row["trial_id"] for row in rows} == {
        "pilot-v001-300000",
        "pilot-v001-600000",
    }
    assert first_payload["requested_model"] == "gemini-2.5-flash-lite"
    assert first_payload["input_data"]["market"]["feature_set"] == "common_candles_v1"
    assert first_payload["input_data"]["execution"]["fee_rate"] == "0.00045"
    assert "account" not in first_payload["input_data"]
    assert manifest["request_count"] == 2
    assert manifest["mode"] == "prepare_only"
    assert len(manifest["source_points_sha256"]) == 64
    assert json.loads(capsys.readouterr().out) == {
        "manifest_path": str(output.with_suffix(".jsonl.manifest.json")),
        "request_count": 2,
        "requests_path": str(output),
        "status": "ok",
        "submission_performed": False,
    }
