"""Run explicitly; the optional dependency is not required by trading-core."""
from decimal import Decimal
from nautilus_parity import compare, core_run

def test_hand_calculated_reference():
    result = core_run()
    assert Decimal(result["gross_realized_pnl"]) == Decimal("10")
    assert Decimal(result["fees"]) == Decimal("0.6")
    assert Decimal(result["cash"]) == Decimal("10009.4")
    assert [Decimal(x["signed_quantity"]) for x in result["fills"]] == [1,1,-1,-1,-1,1]

def test_nautilus_matches_and_repeats():
    first = compare()
    second = compare()
    assert first == second
