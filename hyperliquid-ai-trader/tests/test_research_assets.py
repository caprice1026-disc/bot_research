from __future__ import annotations

import json
from pathlib import Path


def test_checked_in_research_assets_preserve_the_offline_decision_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    prompt_dir = root / "prompts" / "research"
    constitution = (prompt_dir / "constitution.md").read_text(encoding="utf-8")
    trader = (prompt_dir / "trader_v001.md").read_text(encoding="utf-8")
    reviewer = (prompt_dir / "reviewer_v001.md").read_text(encoding="utf-8")
    strategy = json.loads(
        (root / "configs" / "research" / "initial_strategy.json").read_text(encoding="utf-8")
    )

    assert strategy == {
        "schema_version": 2,
        "feature_set": "common_candles_v1",
        "version": 1,
        "parent_version": None,
        "market_hypothesis": "短期BTCでは直近5分の騰落だけでは費用を上回る方向性を得られない。値幅と出来高を合わせ、費用を上回る局面だけを選べるか検証する。これはBinance USD-Mの過去365日から作った検証前の仮説である。",
        "active_rules": [],
        "failure_modes": [
            "無条件の5分momentumとmean reversionは費用控除後に最大DD制限へ到達したため使わない。"
        ],
        "confidence_calibration": {"long": 0.0, "short": 0.0},
        "last_review_cycle": 0,
    }
    assert "過去リターン" in constitution
    assert "将来の期待利益" in constitution
    assert "open_position" in constitution
    assert "口座" in constitution
    assert "would_abstain" in trader
    assert "百分率" in trader
    assert "cumulative_closed_trades" in reviewer
    assert "new_closed_trades" in reviewer
    assert "abstention_reference_outcomes" in reviewer
