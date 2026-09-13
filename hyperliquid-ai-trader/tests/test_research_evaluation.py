from __future__ import annotations

import json
from pathlib import Path

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.data import CommonCandleFeatures, NormalizedCandle, write_normalized_candles_jsonl
from hyperliquid_ai_trader.research.points import PointCandidate, PointSelection, write_point_selection_jsonl


def _candles(count: int) -> list[NormalizedCandle]:
    return [
        NormalizedCandle(
            venue="hyperliquid_mainnet_public",
            symbol="BTC",
            open_time_ms=index * 60_000,
            close_exclusive_ms=(index + 1) * 60_000,
            open=100.0 + index,
            high=100.6 + index,
            low=99.4 + index,
            close=100.2 + index,
            volume=1.0,
            received_at_ms=(index + 1) * 60_000,
            available_at_ms=(index + 1) * 60_000,
            availability_kind="fixture",
        )
        for index in range(count)
    ]


def _point(decision_time_ms: int) -> PointCandidate:
    return PointCandidate(
        decision_time_ms=decision_time_ms,
        venue="hyperliquid_mainnet_public",
        symbol="BTC",
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


def _decision_row(*, decision_time_ms: int, request_hash: str, would_abstain: bool) -> dict[str, object]:
    trial_id = f"trader-v001-{decision_time_ms}"
    return {
        "decision_time_ms": decision_time_ms,
        "request_id": f"{trial_id}:{request_hash}",
        "trial_id": trial_id,
        "request_hash": request_hash,
        "requested_model": "gemini-2.5-flash-lite",
        "returned_model": "gemini-2.5-flash-lite",
        "received_at_ms": decision_time_ms + 1_000,
        "decision": {
            "side": "long",
            "stop_loss_pct": "0.3",
            "take_profit_pct": "0.6",
            "confidence": "0.55",
            "thesis": "fixture",
            "would_abstain": would_abstain,
            "abstain_reason": "weak edge" if would_abstain else None,
        },
    }


def test_evaluate_decisions_cli_keeps_trade_and_shadow_outcomes_separate(tmp_path, capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / "configs" / "research" / "development.json"
    points_path = tmp_path / "points.jsonl"
    points = (_point(0), _point(300_000))
    write_point_selection_jsonl(
        points_path,
        PointSelection(
            points=points,
            stratum_counts={"fixture": 2},
            candidate_stratum_counts={"fixture": 2},
            realized_vol_30m_median=0.006,
        ),
    )
    candles_path = tmp_path / "candles.jsonl"
    write_normalized_candles_jsonl(candles_path, _candles(12))
    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in (
                _decision_row(decision_time_ms=0, request_hash="a" * 64, would_abstain=False),
                _decision_row(decision_time_ms=300_000, request_hash="b" * 64, would_abstain=True),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "evaluation.jsonl"

    assert cli.main(
        [
            "evaluate-decisions",
            "--config",
            str(config),
            "--candles",
            str(candles_path),
            "--points",
            str(points_path),
            "--decisions",
            str(decisions_path),
            "--output",
            str(output),
        ]
    ) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(capsys.readouterr().out)
    assert [row["kind"] for row in rows] == ["trade", "shadow"]
    assert all(row["status"] == "complete" for row in rows)
    assert summary["status"] == "ok"
    assert summary["trade_episodes"] == 1
    assert summary["shadow_episodes"] == 1
    assert summary["incomplete_decisions"] == 0
