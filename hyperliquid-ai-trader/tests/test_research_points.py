from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.data import CommonCandleFeatures
from hyperliquid_ai_trader.research.points import (
    PointCandidate,
    PointSelectionError,
    build_point_candidates,
    select_research_points,
)
from hyperliquid_ai_trader.research.data import (
    CANDLE_INTERVAL_MS,
    RESEARCH_DECISION_INTERVAL_MS,
    NormalizedCandle,
    write_normalized_candles_jsonl,
)


def _candidate(index: int) -> PointCandidate:
    return PointCandidate(
        decision_time_ms=index * RESEARCH_DECISION_INTERVAL_MS,
        features=CommonCandleFeatures(
            feature_set="common_candles_v1",
            as_of_ms=index * RESEARCH_DECISION_INTERVAL_MS,
            return_1m=0.0,
            return_5m=0.01 if index % 2 else -0.01,
            return_15m=0.0,
            return_60m=0.0,
            realized_vol_5m=0.01,
            realized_vol_30m=0.01 if index % 4 < 2 else 0.03,
            atr_pct=0.1,
            volume_zscore=1.0 if index % 4 in {1, 2} else -1.0,
        ),
    )


def _candles(*, count: int = 80) -> list[NormalizedCandle]:
    return [
        NormalizedCandle(
            venue="hyperliquid_mainnet_public",
            symbol="BTC",
            open_time_ms=index * CANDLE_INTERVAL_MS,
            close_exclusive_ms=(index + 1) * CANDLE_INTERVAL_MS,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=100.0 + index,
            received_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            available_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            availability_kind="fixture",
        )
        for index in range(count)
    ]


def test_seeded_point_selection_is_unique_and_pilot_is_prefix_of_full_selection() -> None:
    candidates = [_candidate(index) for index in range(16)]

    pilot = select_research_points(candidates, count=5, seed=42)
    full = select_research_points(candidates, count=12, seed=42)

    pilot_ids = [point.decision_time_ms for point in pilot.points]
    full_ids = [point.decision_time_ms for point in full.points]
    assert pilot_ids == full_ids[:5]
    assert len(set(full_ids)) == 12
    assert sum(full.stratum_counts.values()) == 12
    assert sum(full.candidate_stratum_counts.values()) == 16
    assert full.realized_vol_30m_median == pytest.approx(0.02)


def test_point_selection_rejects_invalid_requested_count_and_insufficient_candidates() -> None:
    candidates = [_candidate(index) for index in range(2)]

    with pytest.raises(PointSelectionError, match="align"):
        PointCandidate(
            decision_time_ms=CANDLE_INTERVAL_MS,
            features=_candidate(0).features,
        )
    with pytest.raises(PointSelectionError, match="at most 500"):
        select_research_points(candidates, count=501, seed=42)
    with pytest.raises(PointSelectionError, match="only 2"):
        select_research_points(candidates, count=3, seed=42)


def test_point_candidates_start_after_61_confirmed_one_minute_candles() -> None:
    candles = _candles()

    candidates = build_point_candidates(candles)

    assert len(candidates) == 4
    assert [candidate.decision_time_ms for candidate in candidates] == [
        65 * CANDLE_INTERVAL_MS,
        70 * CANDLE_INTERVAL_MS,
        75 * CANDLE_INTERVAL_MS,
        80 * CANDLE_INTERVAL_MS,
    ]
    assert all(candidate.decision_time_ms % RESEARCH_DECISION_INTERVAL_MS == 0 for candidate in candidates)
    assert candidates[0].features.as_of_ms == 65 * CANDLE_INTERVAL_MS


def test_points_cli_writes_seeded_features_and_manifest(tmp_path, capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    candles_path = tmp_path / "BTC-1m.jsonl"
    write_normalized_candles_jsonl(candles_path, _candles())
    output = tmp_path / "points.jsonl"

    assert cli.main(
        [
            "points",
            "--config",
            str(root / "configs" / "research" / "development.json"),
            "--candles",
            str(candles_path),
            "--count",
            "3",
            "--output",
            str(output),
        ]
    ) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert all(row["features"]["feature_set"] == "common_candles_v1" for row in rows)
    assert manifest["seed"] == 42
    assert manifest["point_count"] == 3
    assert manifest["decision_interval_ms"] == RESEARCH_DECISION_INTERVAL_MS
    assert sum(manifest["candidate_stratum_counts"].values()) == manifest["candidate_count"]
    assert sum(manifest["selected_stratum_counts"].values()) == manifest["point_count"]
    assert len(manifest["candidate_set_sha256"]) == 64
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_points_cli_reports_insufficient_data_without_writing_an_empty_artifact(tmp_path, capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    candles_path = tmp_path / "BTC-1m.jsonl"
    write_normalized_candles_jsonl(candles_path, _candles(count=60))
    output = tmp_path / "points.jsonl"

    assert cli.main(
        [
            "points",
            "--config",
            str(root / "configs" / "research" / "development.json"),
            "--candles",
            str(candles_path),
            "--count",
            "1",
            "--output",
            str(output),
        ]
    ) == 3

    assert not output.exists()
    assert json.loads(capsys.readouterr().out) == {
        "candidate_count": 0,
        "reason": "insufficient_point_candidates",
        "requested_count": 1,
        "status": "insufficient_data",
    }
