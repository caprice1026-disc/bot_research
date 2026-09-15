from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from hyperliquid_ai_trader.research.binance import FundingSeries
from hyperliquid_ai_trader.research.conditional_edge import CandidateSpec, load_conditional_study_config
from hyperliquid_ai_trader.research.conditional_replay import run_candidate_replay
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS

from test_research_conditional_edge import _candles, _study_path


def test_candidate_replay_applies_a_pending_exit_only_at_its_exit_time(tmp_path) -> None:
    study = replace(
        load_conditional_study_config(_study_path(tmp_path)),
        start_ms=0,
        end_ms=140 * CANDLE_INTERVAL_MS,
    )
    candles = [
        replace(
            candle,
            open=100 + index * 0.1,
            high=100 + index * 0.1 + 0.01,
            low=100 + index * 0.1 - 0.01,
            close=100 + index * 0.1,
        )
        for index, candle in enumerate(_candles(180))
    ]

    result = run_candidate_replay(
        candles=candles,
        funding=FundingSeries(()),
        study_config=study,
        candidate=CandidateSpec("A", 1800000, "hold_only", None, Decimal("13")),
        thresholds={},
    )

    first = next(row for row in result.decisions if row["status"] == "executed")
    blocked = next(
        row
        for row in result.decisions
        if row["decision_time_ms"] > first["decision_time_ms"] and row["status"] == "position_blocked"
    )
    assert blocked["equity_before"] == "1000"
    assert result.episodes[0].net_pnl > Decimal("0")
