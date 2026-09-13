from decimal import Decimal
import json

from pathlib import Path

from hyperliquid_ai_trader.models import Side, TradeDecision
from hyperliquid_ai_trader.research.config import load_research_config
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS, NormalizedCandle
from hyperliquid_ai_trader.research.data import write_normalized_candles_jsonl
from hyperliquid_ai_trader.research.evaluation import ValidatedPointDecision
from hyperliquid_ai_trader.research.replay import run_replay
from hyperliquid_ai_trader.research import cli

from hyperliquid_ai_trader.research.risk import ResearchRiskEngine
from hyperliquid_ai_trader.research.simulator import VirtualAccount


def _replay_decision(time_ms: int, suffix: str) -> ValidatedPointDecision:
    return ValidatedPointDecision(
        decision_time_ms=time_ms,
        request_id=f"{suffix}:" + "a" * 64,
        trial_id=suffix,
        request_hash="a" * 64,
        requested_model="gemini-2.5-flash-lite",
        returned_model="gemini-2.5-flash-lite",
        received_at_ms=time_ms + 1_000,
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


def test_replay_is_partial_when_one_decision_lacks_price_window() -> None:
    config = load_research_config(Path(__file__).parents[1] / "configs/research/development.json")
    candles = [
        NormalizedCandle("hyperliquid_mainnet_public", "BTC", i * CANDLE_INTERVAL_MS, (i + 1) * CANDLE_INTERVAL_MS, 100, 101, 99, 100, 1, (i + 1) * CANDLE_INTERVAL_MS, (i + 1) * CANDLE_INTERVAL_MS, "fixture")
        for i in range(7)
    ]
    risk = ResearchRiskEngine(
        risk_per_trade_pct=config.risk_per_trade_pct, max_daily_loss_pct=config.max_daily_loss_pct,
        max_drawdown_pct=config.max_drawdown_pct, max_position_notional_usd=config.max_position_notional_usd,
        leverage=config.leverage, min_notional_usd=config.min_notional_usd,
    )
    result = run_replay(
        candles=candles,
        decisions=[_replay_decision(0, "a"), _replay_decision(600_000, "b")],
        config=config,
        risk=risk,
    )
    assert result.status == "partial"
    assert [event["status"] for event in result.events] == ["trade", "incomplete"]


def test_replay_cli_preserves_partial_status_for_saved_jsonl(tmp_path) -> None:
    root = Path(__file__).parents[1]
    config_path = root / "configs/research/development.json"
    candles_path = tmp_path / "candles.jsonl"
    decisions_path = tmp_path / "decisions.jsonl"
    output_path = tmp_path / "replay.jsonl"
    candles = [
        NormalizedCandle("hyperliquid_mainnet_public", "BTC", i * CANDLE_INTERVAL_MS, (i + 1) * CANDLE_INTERVAL_MS, 100, 101, 99, 100, 1, (i + 1) * CANDLE_INTERVAL_MS, (i + 1) * CANDLE_INTERVAL_MS, "fixture")
        for i in range(7)
    ]
    write_normalized_candles_jsonl(candles_path, candles)
    decisions = [_replay_decision(0, "a"), _replay_decision(600_000, "b")]
    decisions_path.write_text(
        "".join(json.dumps({
            "decision_time_ms": record.decision_time_ms,
            "request_id": record.request_id,
            "trial_id": record.trial_id,
            "request_hash": record.request_hash,
            "requested_model": record.requested_model,
            "returned_model": record.returned_model,
            "received_at_ms": record.received_at_ms,
            "decision": {
                "side": record.decision.side.value,
                "stop_loss_pct": format(record.decision.stop_loss_pct, "f"),
                "take_profit_pct": format(record.decision.take_profit_pct, "f"),
                "confidence": format(record.decision.confidence, "f"),
                "thesis": record.decision.thesis,
                "would_abstain": record.decision.would_abstain,
                "abstain_reason": record.decision.abstain_reason,
            },
        }) + "\n" for record in decisions),
        encoding="utf-8",
    )
    assert cli.main([
        "replay", "--config", str(config_path), "--candles", str(candles_path),
        "--decisions", str(decisions_path), "--output", str(output_path),
    ]) == 3
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["trade", "incomplete"]


def test_virtual_account_resets_daily_state_at_utc_boundary_without_trade() -> None:
    account = VirtualAccount(Decimal("1000"), Decimal("1000"))
    account.daily_realized_pnl = Decimal("-10")
    account.utc_day = 0
    account.advance_time(86_400_000)
    assert account.day_start_equity == Decimal("1000")
    assert account.daily_realized_pnl == Decimal("0")


def test_risk_engine_blocks_existing_position_and_daily_loss() -> None:
    engine = ResearchRiskEngine(
        risk_per_trade_pct=Decimal("1"), max_daily_loss_pct=Decimal("2"),
        max_drawdown_pct=Decimal("25"), max_position_notional_usd=Decimal("250"),
        leverage=Decimal("5"), min_notional_usd=Decimal("10"),
    )
    account = VirtualAccount(Decimal("1000"), Decimal("1000"))
    account.daily_realized_pnl = Decimal("-20")
    assert engine.check_entry(account=account, entry_price=Decimal("100"), stop_loss_pct=Decimal("1"), position_open=True).reason == "position_open"
    assert engine.check_entry(account=account, entry_price=Decimal("100"), stop_loss_pct=Decimal("1"), position_open=False).reason == "daily_loss_limit"
