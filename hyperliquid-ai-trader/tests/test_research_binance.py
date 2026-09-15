from __future__ import annotations

import csv
from decimal import Decimal
from importlib import import_module
import json
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.baseline import run_baseline
from hyperliquid_ai_trader.research.config import load_research_config
from hyperliquid_ai_trader.research.data import (
    CANDLE_INTERVAL_MS,
    NormalizedCandle,
    write_normalized_candles_jsonl,
)
from hyperliquid_ai_trader.research.evaluation import (
    ValidatedPointDecision,
    evaluate_validated_decisions,
)
from hyperliquid_ai_trader.research.points import PointCandidate
from hyperliquid_ai_trader.research.risk import ResearchRiskEngine
from hyperliquid_ai_trader.models import Side, TradeDecision


def _binance_module():
    return import_module("hyperliquid_ai_trader.research.binance")


def test_cli_exposes_a_binance_csv_import_boundary() -> None:
    parser = cli._parser()

    assert "import-binance-csv" in parser._subparsers._group_actions[0].choices


def _csv_row(index: int) -> dict[str, str]:
    open_time_ms = index * CANDLE_INTERVAL_MS
    price = Decimal("100") + Decimal(index) / Decimal("100")
    return {
        "open_time_utc": "fixture",
        "open_time_ms": str(open_time_ms),
        "open": format(price, "f"),
        "high": format(price + Decimal("0.01"), "f"),
        "low": format(price - Decimal("0.01"), "f"),
        "close": format(price, "f"),
        "volume": "1",
        "close_time_ms": str(open_time_ms + CANDLE_INTERVAL_MS - 1),
        "quote_asset_volume": "1",
        "number_of_trades": "1",
        "taker_buy_base_asset_volume": "1",
        "taker_buy_quote_asset_volume": "1",
        "ignore": "0",
    }


def _write_binance_csv(path: Path, *, count: int) -> None:
    rows = [_csv_row(index) for index in range(count)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _config(*, venue: str, symbol: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "fixture-v1",
        "market": {"venue": venue, "symbol": symbol, "interval": "1m"},
        "feature_set": "common_candles_v1",
        "api": {
            "allow_paid_api": False,
            "budget_usd": "0",
            "trader_model": "gemini-2.5-flash-lite",
            "reviewer_model": "gemini-3.6-flash",
        },
        "execution": {
            "model_delay_ms": 1_000,
            "max_arrival_delay_ms": 60_000,
            "max_hold_ms": 300_000,
            "fee_rate": "0",
            "spread_bps": "0",
            "slippage_bps": "0",
        },
        "decision": {
            "min_stop_loss_pct": "0.10",
            "max_stop_loss_pct": "1.00",
            "min_take_profit_pct": "0.10",
            "max_take_profit_pct": "2.00",
        },
        "simulation": {"initial_equity": "1000", "reference_notional": "250"},
    }


def test_binance_csv_import_preserves_the_confirmed_one_minute_boundary(tmp_path) -> None:
    source = tmp_path / "BTCUSDT-1m-365d.csv"
    _write_binance_csv(source, count=2)
    binance = _binance_module()

    candles = binance.read_binance_usdm_1m_csv(source, delivery_delay_ms=250)

    assert [(candle.venue, candle.symbol) for candle in candles] == [
        ("binance_usdm_public", "BTCUSDT"),
        ("binance_usdm_public", "BTCUSDT"),
    ]
    assert candles[0].close_exclusive_ms == CANDLE_INTERVAL_MS
    assert candles[0].available_at_ms == CANDLE_INTERVAL_MS + 250


def test_cli_imports_verified_binance_csv_with_provenance_manifest(tmp_path, capsys) -> None:
    source = tmp_path / "BTCUSDT-1m-365d.csv"
    _write_binance_csv(source, count=2)
    config_path = tmp_path / "binance.json"
    config_path.write_text(
        json.dumps(_config(venue="binance_usdm_public", symbol="BTCUSDT")), encoding="utf-8"
    )
    output = tmp_path / "candles.jsonl"

    assert cli.main(
        [
            "import-binance-csv",
            "--config",
            str(config_path),
            "--input",
            str(source),
            "--delivery-delay-ms",
            "250",
            "--output",
            str(output),
        ]
    ) == 0

    manifest = json.loads(output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert manifest["mode"] == "import_binance_usdm_1m"
    assert manifest["source_csv_sha256"]


def test_binance_baseline_refuses_to_treat_missing_funding_as_zero(tmp_path) -> None:
    source = tmp_path / "BTCUSDT-1m-365d.csv"
    _write_binance_csv(source, count=72)
    candles = _binance_module().read_binance_usdm_1m_csv(source)
    simulator = import_module("hyperliquid_ai_trader.research.simulator")

    result = run_baseline(
        candles=candles,
        baseline_name="momentum",
        execution_config=simulator.ExecutionConfig(
            fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0")
        ),
        initial_equity=Decimal("1000"),
        risk=ResearchRiskEngine(
            risk_per_trade_pct=Decimal("1"),
            max_daily_loss_pct=Decimal("20"),
            max_drawdown_pct=Decimal("25"),
            max_position_notional_usd=Decimal("250"),
            leverage=Decimal("5"),
            min_notional_usd=Decimal("10"),
        ),
        market_venue="binance_usdm_public",
        symbol="BTCUSDT",
    )

    assert result.status == "insufficient_data"
    assert result.incomplete_funding_decisions == 1
    assert result.final_equity == Decimal("1000")


def test_candle_series_matches_the_generic_features_and_finds_entry_by_index() -> None:
    candles = [
        NormalizedCandle(
            venue="binance_usdm_public",
            symbol="BTCUSDT",
            open_time_ms=index * CANDLE_INTERVAL_MS,
            close_exclusive_ms=(index + 1) * CANDLE_INTERVAL_MS,
            open=100 + index * 0.01,
            high=100.01 + index * 0.01,
            low=99.99 + index * 0.01,
            close=100 + index * 0.01,
            volume=1,
            received_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            available_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            availability_kind="fixture",
        )
        for index in range(70)
    ]
    data = import_module("hyperliquid_ai_trader.research.data")
    simulator = import_module("hyperliquid_ai_trader.research.simulator")
    series = data.CandleSeries(candles)
    decision_time_ms = 65 * CANDLE_INTERVAL_MS

    assert series.build_features(decision_time_ms=decision_time_ms) == data.build_common_candle_features(
        candles=candles,
        decision_time_ms=decision_time_ms,
    )
    assert series.entry_index(
        decision_time_ms=decision_time_ms,
        config=simulator.ExecutionConfig(),
    ) == 66


def test_funding_series_rejects_a_missing_scheduled_payment() -> None:
    binance = _binance_module()
    funding = binance.FundingSeries((binance.FundingEvent(timestamp_ms=0, rate=Decimal("0.0001")),))

    with pytest.raises(binance.FundingDataError, match="missing funding event"):
        funding.payment(
            entry_time_ms=7 * 60 * 60 * 1_000 + 56 * 60 * 1_000,
            exit_time_ms=8 * 60 * 60 * 1_000 + 60 * 1_000,
            notional=Decimal("250"),
            side="long",
        )


def test_funding_series_applies_the_binance_long_short_sign_convention() -> None:
    binance = _binance_module()
    funding = binance.FundingSeries(
        (binance.FundingEvent(timestamp_ms=8 * 60 * 60 * 1_000 + 1, rate=Decimal("0.0001")),)
    )
    payment = {
        side: funding.payment(
            entry_time_ms=7 * 60 * 60 * 1_000 + 56 * 60 * 1_000,
            exit_time_ms=8 * 60 * 60 * 1_000 + 60 * 1_000,
            notional=Decimal("250"),
            side=side,
        )
        for side in ("long", "short")
    }

    assert payment == {"long": Decimal("-0.0250"), "short": Decimal("0.0250")}


def _point(decision_time_ms: int) -> PointCandidate:
    from hyperliquid_ai_trader.research.data import CommonCandleFeatures

    return PointCandidate(
        decision_time_ms=decision_time_ms,
        venue="binance_usdm_public",
        symbol="BTCUSDT",
        features=CommonCandleFeatures(
            feature_set="common_candles_v1",
            as_of_ms=decision_time_ms,
            return_1m=0,
            return_5m=0,
            return_15m=0,
            return_60m=0,
            realized_vol_5m=0,
            realized_vol_30m=0,
            atr_pct=0,
            volume_zscore=0,
        ),
    )


def _decision(decision_time_ms: int) -> ValidatedPointDecision:
    return ValidatedPointDecision(
        decision_time_ms=decision_time_ms,
        request_id=f"fixture-{decision_time_ms}:" + "a" * 64,
        trial_id=f"fixture-{decision_time_ms}",
        request_hash="a" * 64,
        requested_model="gemini-2.5-flash-lite",
        returned_model="gemini-2.5-flash-lite",
        received_at_ms=decision_time_ms,
        decision=TradeDecision(
            side=Side.LONG,
            stop_loss_pct=Decimal("0.3"),
            take_profit_pct=Decimal("0.6"),
            confidence=Decimal("0.5"),
            thesis="fixture",
            would_abstain=False,
            abstain_reason=None,
        ),
    )


def test_point_evaluation_keeps_completed_rows_when_one_funding_event_is_missing(tmp_path) -> None:
    source = tmp_path / "BTCUSDT-1m-365d.csv"
    _write_binance_csv(source, count=482)
    config_path = tmp_path / "binance.json"
    config_path.write_text(
        json.dumps(_config(venue="binance_usdm_public", symbol="BTCUSDT")), encoding="utf-8"
    )
    binance = _binance_module()
    evaluation = evaluate_validated_decisions(
        candles=binance.read_binance_usdm_1m_csv(source),
        points=[_point(0), _point(7 * 60 * 60 * 1_000 + 55 * 60 * 1_000)],
        decisions=[_decision(0), _decision(7 * 60 * 60 * 1_000 + 55 * 60 * 1_000)],
        config=load_research_config(config_path),
        funding=binance.FundingSeries(()),
    )

    assert evaluation.status == "partial"
    assert [outcome.status for outcome in evaluation.outcomes] == ["complete", "incomplete_funding"]
    assert evaluation.public_summary()["incomplete_funding_decisions"] == 1


def test_baseline_cli_rejects_candles_that_do_not_match_the_configured_market(tmp_path) -> None:
    candles = [
        NormalizedCandle(
            venue="hyperliquid_mainnet_public",
            symbol="BTC",
            open_time_ms=index * CANDLE_INTERVAL_MS,
            close_exclusive_ms=(index + 1) * CANDLE_INTERVAL_MS,
            open=100,
            high=100.1,
            low=99.9,
            close=100,
            volume=1,
            received_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            available_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            availability_kind="fixture",
        )
        for index in range(72)
    ]
    candles_path = tmp_path / "candles.jsonl"
    write_normalized_candles_jsonl(candles_path, candles)
    config_path = tmp_path / "binance.json"
    config_path.write_text(json.dumps(_config(venue="binance_usdm_public", symbol="BTCUSDT")), encoding="utf-8")

    assert cli.main(
        [
            "baseline",
            "--config",
            str(config_path),
            "--candles",
            str(candles_path),
            "--baseline",
            "momentum",
        ]
    ) == 2
