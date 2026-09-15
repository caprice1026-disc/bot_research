from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research.conditional_edge import (
    ConditionalStudyError,
    CandidateSpec,
    candidate_decision,
    derive_regime_features,
    fit_regime_thresholds,
    iter_conditional_labels,
    load_conditional_study_config,
    validate_study_inputs,
    write_conditional_labels,
)
from hyperliquid_ai_trader.research.binance import FUNDING_INTERVAL_MS, FundingEvent, FundingSeries
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS, NormalizedCandle


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "conditional-fixture-v2",
        "base_config": "base.json",
        "period": {
            "start_inclusive": "2025-09-01T00:00:00Z",
            "end_exclusive": "2026-09-01T00:00:00Z",
        },
        "decision_interval_ms": 300000,
        "holds_ms": [300000, 600000, 900000, 1800000],
        "feature_set": "common_candles_v1",
        "exit_profiles": ["hold_only", "fixed_sl_tp"],
        "folds": {
            "exploration_end_exclusive": "2026-03-01T00:00:00Z",
            "validation_end_exclusive": "2026-07-01T00:00:00Z",
            "confirmation_start_inclusive": "2026-07-01T00:00:00Z",
        },
        "quantiles": ["0.25", "0.50", "0.75"],
        "candidate_specs": ["A", "B", "C", "D", "E"],
        "promotion_policy": {
            "minimum_validation_trades": 200,
            "minimum_side_trades": 50,
            "positive_validation_month_fraction": "0.5",
            "max_drawdown_pct": "25",
        },
    }


def _study_path(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "base.json").write_text(
        (root / "configs" / "research" / "binance_development.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    path = tmp_path / "study.json"
    path.write_text(json.dumps(payload or _payload()), encoding="utf-8")
    return path


def test_conditional_study_config_resolves_base_and_time_contract(tmp_path: Path) -> None:
    study = load_conditional_study_config(_study_path(tmp_path))

    assert study.base_config_path == tmp_path / "base.json"
    assert study.holds_ms == (300000, 600000, 900000, 1800000)
    assert study.exit_profiles == ("hold_only", "fixed_sl_tp")
    assert study.exploration_end_ms == 1772323200000
    assert study.confirmation_start_ms == 1782864000000


def test_conditional_study_config_rejects_non_minute_hold(tmp_path: Path) -> None:
    payload = _payload()
    payload["holds_ms"] = [300001]

    with pytest.raises(ConditionalStudyError, match="whole minutes"):
        load_conditional_study_config(_study_path(tmp_path, payload))


def _candles(count: int) -> list[NormalizedCandle]:
    return [
        NormalizedCandle(
            venue="binance_usdm_public",
            symbol="BTCUSDT",
            open_time_ms=index * CANDLE_INTERVAL_MS,
            close_exclusive_ms=(index + 1) * CANDLE_INTERVAL_MS,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
            received_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            available_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            availability_kind="fixture",
        )
        for index in range(count)
    ]


def test_conditional_labels_keep_incomplete_slots_and_all_profiles(tmp_path: Path) -> None:
    study = replace(
        load_conditional_study_config(_study_path(tmp_path)),
        start_ms=0,
        end_ms=120 * CANDLE_INTERVAL_MS,
    )

    labels = list(
        iter_conditional_labels(
            candles=_candles(147),
            funding=FundingSeries(()),
            study_config=study,
        )
    )

    assert len(labels) == 24 * 16
    assert all(label.status == "incomplete_price" for label in labels[: 13 * 16])
    complete = [label for label in labels if label.status == "complete"]
    assert len(complete) == 11 * 16
    hold_only = next(
        label
        for label in complete
        if label.exit_profile == "hold_only" and label.hold_ms == 300000
    )
    assert hold_only.episode is not None
    assert hold_only.episode.exit_reason == "max_hold"
    assert hold_only.episode.net_pnl < Decimal("0")


def test_conditional_label_writer_separates_features_and_future_outcomes(tmp_path: Path) -> None:
    study = replace(
        load_conditional_study_config(_study_path(tmp_path)),
        start_ms=0,
        end_ms=120 * CANDLE_INTERVAL_MS,
    )

    result = write_conditional_labels(
        output_dir=tmp_path / "output",
        candles=_candles(147),
        funding=FundingSeries(()),
        study_config=study,
        candle_sha256="c" * 64,
        funding_sha256="f" * 64,
        code_commit_sha="d" * 40,
    )

    features = [json.loads(line) for line in result.features_path.read_text(encoding="utf-8").splitlines()]
    labels = [json.loads(line) for line in result.labels_path.read_text(encoding="utf-8").splitlines()]
    assert len(features) == 11
    assert len(labels) == 24 * 16
    assert "net_pnl" not in features[0]
    assert labels[0]["net_pnl"] is None
    manifest = json.loads(result.labels_manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "conditional_labels"
    assert manifest["quality"] == "partial"


def test_input_validation_normalizes_funding_timestamps_to_their_eight_hour_slot(tmp_path: Path) -> None:
    study = replace(
        load_conditional_study_config(_study_path(tmp_path)),
        start_ms=0,
        end_ms=3 * FUNDING_INTERVAL_MS,
    )

    summary = validate_study_inputs(
        candles=_candles(1_500),
        funding=FundingSeries(
            (
                FundingEvent(0, Decimal("0")),
                FundingEvent(FUNDING_INTERVAL_MS + 1, Decimal("0")),
                FundingEvent(2 * FUNDING_INTERVAL_MS + 1, Decimal("0")),
            )
        ),
        study_config=study,
    )

    assert summary["expected_funding_slots"] == 3


def test_regime_ratios_and_candidates_use_only_declared_features(tmp_path: Path) -> None:
    study = load_conditional_study_config(_study_path(tmp_path))
    features = {
        "return_15m": 0.01,
        "return_60m": 0.02,
        "return_5m": -0.90,
        "realized_vol_5m": 0.002,
        "realized_vol_30m": 0.001,
        "atr_pct": 0.10,
        "volume_zscore": 1.0,
    }

    regime = derive_regime_features(features, study.base_config.execution)
    assert regime["alignment"] == "up"
    assert regime["atr_bps"] == Decimal("10.0")
    assert regime["atr_to_cost"] == Decimal("10.0") / Decimal("13.0000")
    assert regime["vol_expansion_ratio"] == Decimal("2")
    thresholds = fit_regime_thresholds([regime], (Decimal("0.5"),))
    candidate = CandidateSpec("A", 300000, "fixed_sl_tp", None, Decimal("13"))
    assert candidate_decision(candidate, features, thresholds).side.value == "long"
    assert candidate_decision(
        candidate,
        {**features, "return_5m": 0.90},
        thresholds,
    ).side.value == "long"
    assert derive_regime_features(
        {**features, "realized_vol_30m": 0.0}, study.base_config.execution
    )["vol_expansion_ratio"] is None
