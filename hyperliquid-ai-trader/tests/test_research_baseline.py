from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

from hyperliquid_ai_trader.research.baseline import run_baseline
from hyperliquid_ai_trader.research.cli import main
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS, NormalizedCandle
from hyperliquid_ai_trader.research.data import write_normalized_candles_jsonl
from hyperliquid_ai_trader.research.simulator import ExecutionConfig


def _candles(count: int) -> list[NormalizedCandle]:
    candles: list[NormalizedCandle] = []
    for index in range(count):
        price = 100.0 + index * 0.01
        open_time_ms = index * CANDLE_INTERVAL_MS
        candles.append(
            NormalizedCandle(
                venue="hyperliquid_mainnet_public",
                symbol="BTC",
                open_time_ms=open_time_ms,
                close_exclusive_ms=open_time_ms + CANDLE_INTERVAL_MS,
                open=price,
                high=price + 0.001,
                low=price - 0.001,
                close=price,
                volume=1.0,
                received_at_ms=open_time_ms + CANDLE_INTERVAL_MS,
                available_at_ms=open_time_ms + CANDLE_INTERVAL_MS,
                availability_kind="fixture",
            )
        )
    return candles


def _config() -> ExecutionConfig:
    return ExecutionConfig(
        model_delay_ms=0,
        max_arrival_delay_ms=60_000,
        max_hold_ms=300_000,
        fee_rate=Decimal("0"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )


def test_momentum_baseline_replays_in_time_order_without_overlapping_equity() -> None:
    result = run_baseline(
        candles=_candles(67),
        baseline_name="momentum",
        execution_config=_config(),
        initial_equity=Decimal("1000"),
        reference_notional=Decimal("250"),
    )

    assert result.status == "ok"
    assert len(result.episodes) == 1
    assert result.episodes[0].entry_time_ms == 61 * CANDLE_INTERVAL_MS
    assert result.episodes[0].exit_time_ms == 66 * CANDLE_INTERVAL_MS
    assert result.final_equity > Decimal("1000")
    assert result.position_blocked > 0


def test_baseline_reports_insufficient_data_instead_of_zero_trade_success() -> None:
    result = run_baseline(
        candles=_candles(66),
        baseline_name="momentum",
        execution_config=_config(),
        initial_equity=Decimal("1000"),
        reference_notional=Decimal("250"),
    )

    assert result.status == "insufficient_data"
    assert result.episodes == ()
    assert result.incomplete_decisions > 0


def test_baseline_cli_runs_offline_from_normalized_jsonl(tmp_path, capsys) -> None:
    candles_path = tmp_path / "candles.jsonl"
    # The checked-in 1s model delay skips the boundary immediately after a
    # decision, so one full 300s episode needs candles through index 67.
    write_normalized_candles_jsonl(candles_path, _candles(68))
    config_path = Path(__file__).resolve().parents[1] / "configs" / "research" / "development.json"

    assert main([
        "baseline",
        "--config", str(config_path),
        "--candles", str(candles_path),
        "--baseline", "momentum",
    ]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert summary["episodes"] == 1


def test_baseline_cli_reports_empty_candle_file_as_insufficient_data(tmp_path, capsys) -> None:
    candles_path = tmp_path / "empty.jsonl"
    candles_path.write_text("", encoding="utf-8")
    config_path = Path(__file__).resolve().parents[1] / "configs" / "research" / "development.json"

    assert main([
        "baseline",
        "--config", str(config_path),
        "--candles", str(candles_path),
        "--baseline", "momentum",
    ]) == 3

    assert json.loads(capsys.readouterr().out)["status"] == "insufficient_data"
