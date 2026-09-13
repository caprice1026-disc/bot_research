from decimal import Decimal

import pytest

from hyperliquid_ai_trader.research.forward import run_paired_forward
from hyperliquid_ai_trader.research.freeze import FreezeError, build_freeze_manifest, freeze_fingerprint, verify_freeze
from hyperliquid_ai_trader.research.testnet_audit import AuditError, AuditFill, actual_stop_risk, max_hold_deadline, protection_size_for_fill


def test_freeze_detects_code_or_config_drift() -> None:
    frozen = build_freeze_manifest(config={"experiment_id": "e", "x": 1}, code_commit_sha="abc", dates={"start": 1})
    assert freeze_fingerprint(frozen)
    verify_freeze(frozen, dict(frozen))
    changed = dict(frozen, code_commit_sha="def")
    with pytest.raises(FreezeError):
        verify_freeze(frozen, changed)


def test_forward_uses_same_snapshot_for_both_arms() -> None:
    seen = []
    result = run_paired_forward(
        [{"slot_id": "s1", "snapshot": {"close": 10}}],
        static_strategy={"version": 1}, adaptive_strategy={"version": 2},
        static_runner=lambda strategy, snapshot: seen.append(("s", snapshot)) or strategy["version"],
        adaptive_runner=lambda strategy, snapshot: seen.append(("a", snapshot)) or strategy["version"],
    )
    assert result[0].static_value == 1 and result[0].adaptive_value == 2
    assert seen[0][1] is seen[1][1]


def test_audit_uses_actual_fill_for_risk_and_hold() -> None:
    fill = AuditFill("long", Decimal("2"), Decimal("1.5"), Decimal("101"), 100)
    assert protection_size_for_fill(requested_size=fill.requested_size, filled_size=fill.filled_size) == Decimal("1.5")
    assert actual_stop_risk(side="long", entry_price=fill.average_price, stop_price=Decimal("100")) > 0
    assert max_hold_deadline(filled_at_ms=100, max_hold_ms=50) == 150
    with pytest.raises(AuditError):
        protection_size_for_fill(requested_size=Decimal("1"), filled_size=Decimal("2"))
