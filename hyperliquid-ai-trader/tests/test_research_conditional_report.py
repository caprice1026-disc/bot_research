from __future__ import annotations

from dataclasses import replace
import json

from hyperliquid_ai_trader.research.binance import FundingSeries
from hyperliquid_ai_trader.research.conditional_edge import write_conditional_labels
from hyperliquid_ai_trader.research.conditional_report import (
    analyze_conditional_study,
    write_conditional_replays,
)
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS

from test_research_conditional_edge import _candles, _study_path
from hyperliquid_ai_trader.research.conditional_edge import load_conditional_study_config


def test_analysis_uses_feature_artifacts_and_preserves_partial_coverage(tmp_path) -> None:
    study = replace(
        load_conditional_study_config(_study_path(tmp_path)),
        start_ms=0,
        exploration_end_ms=120 * CANDLE_INTERVAL_MS,
        validation_end_ms=200 * CANDLE_INTERVAL_MS,
        confirmation_start_ms=200 * CANDLE_INTERVAL_MS,
        end_ms=300 * CANDLE_INTERVAL_MS,
    )
    input_dir = tmp_path / "input"
    write_conditional_labels(
        output_dir=input_dir,
        candles=[
            replace(
                candle,
                open=100 + index * 0.1,
                high=100 + index * 0.1 + 0.01,
                low=100 + index * 0.1 - 0.01,
                close=100 + index * 0.1,
            )
            for index, candle in enumerate(_candles(340))
        ],
        funding=FundingSeries(()),
        study_config=study,
        candle_sha256="c" * 64,
        funding_sha256="f" * 64,
        code_commit_sha="d" * 40,
    )

    result = analyze_conditional_study(
        study_config=study,
        input_dir=input_dir,
        output_dir=tmp_path / "analysis",
    )

    candidates = json.loads(result.candidates_path.read_text(encoding="utf-8"))
    assert result.quality == "partial"
    assert len(candidates["selected_candidate_ids"]) == 4
    assert candidates["candidates"][0]["fold_stats"]["validation"]["trade_count"] >= 0

    replay = write_conditional_replays(
        study_config=study,
        candles=[
            replace(
                candle,
                open=100 + index * 0.1,
                high=100 + index * 0.1 + 0.01,
                low=100 + index * 0.1 - 0.01,
                close=100 + index * 0.1,
            )
            for index, candle in enumerate(_candles(340))
        ],
        funding=FundingSeries(()),
        analysis_dir=tmp_path / "analysis",
        output_dir=tmp_path / "replay",
    )
    assert replay.status == "partial"
    assert replay.episodes_path.read_text(encoding="utf-8")
    assert json.loads(replay.report_path.read_text(encoding="utf-8"))["conclusion"] == "inconclusive"
