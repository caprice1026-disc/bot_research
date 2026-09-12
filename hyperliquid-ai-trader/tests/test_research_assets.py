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
        "version": 1,
        "parent_version": None,
        "market_hypothesis": "1分OHLCVのモメンタム、ボラティリティ、出来高の組合せは、費用考慮後の方向性に関する検証対象の仮説である。",
        "active_rules": [],
        "failure_modes": [],
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
