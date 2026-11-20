"""Live safety boundaries verified against a deterministic exchange, without network."""
from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from position_stg.config import Config
from position_stg.store import SQLiteStore
from position_stg.testnet import TestnetController as Controller, TestnetExchange as Adapter, coverage
from test_testnet import Exchange


OPEN = {"intent": "set_target", "target_multiple": "1", "stop_price": "99", "reason": "test"}
HOLD = {"intent": "hold", "target_multiple": None, "stop_price": None, "reason": "test"}
CLOSE = {"intent": "set_target", "target_multiple": "0", "stop_price": None, "reason": "test"}


def setup(tmp_path, exchange=None):
    exchange = exchange or Exchange()
    clock = [300.0]
    store = SQLiteStore(tmp_path / "live.db")
    controller = Controller(Config(), store, exchange, clock=lambda: clock[0])
    return clock, store, exchange, controller


def test_maintain_does_not_claim_slot_and_hold_keeps_position_across_restart(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    assert ctl.maintain()["status"] == "checked"
    assert store.read("runtime")[0]["last_cycle"] == -1
    assert ctl.tick(OPEN, 300)["status"] == "checked"
    qty, sends = ex.quantity, len(ex.sent)
    assert qty > 0
    # New SL ownership must already be durable, not discovered next cycle.
    assert {o["oid"] for o in ex.orders} <= set(store.read("runtime")[0]["owned_oids"])
    for at in (600, 900, 1200):
        clock[0] = at
        ctl = Controller(Config(), SQLiteStore(store.path), ex, clock=lambda: clock[0])
        assert ctl.maintain()["status"] == "checked"
        assert ctl.tick(HOLD, at)["status"] == "checked"
        assert ex.quantity == qty and len(ex.sent) == sends
        assert not store.read("runtime")[0]["manual_pause"]
    assert store.read("runtime")[0]["account"]["opened_at"] == 300


def test_duplicate_does_not_resubmit_but_repairs_missing_stop(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    qty, sends = ex.quantity, len(ex.sent)
    assert ctl.tick(OPEN, 300)["status"] == "duplicate"
    assert len(ex.sent) == sends
    ex.orders.clear()
    result = ctl.tick(OPEN, 300)
    assert result["status"] == "duplicate" and ex.quantity == qty
    assert len(ex.sent) == sends + 1 and ex.sent[-1]["stop"] is not None
    assert coverage(result["snapshot"])[0] == qty


def test_missing_stop_repair_failure_closes_only_managed_exposure(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    ex.orders.clear()
    ex.reject_stops = True
    clock[0] = 315
    result = ctl.maintain()
    assert ex.quantity == 0 and ex.sent[-1]["reduce_only"]
    assert D(result["snapshot"]["quantity"]) == 0
    assert D(store.read("runtime")[0]["account"]["quantity"]) == 0
    assert store.read("runtime")[0]["last_cycle"] == 300


def test_replacement_stop_is_confirmed_before_old_stop_cancel(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    old_oid = ex.orders[0]["oid"]
    original_cancel = ex.cancel
    def cancel(oid):
        if oid == old_oid:
            assert any(D(o["triggerPx"]) == D("99.5") and o["oid"] != oid for o in ex.orders)
        return original_cancel(oid)
    ex.cancel = cancel
    clock[0] = 600
    result = ctl.tick({**HOLD, "stop_price": "99.5"}, 600)
    assert result["status"] == "checked"
    assert all(o["oid"] != old_oid for o in ex.orders)
    assert D(store.read("runtime")[0]["stop"]) == D("99.5")
    assert store.items("cancel-")


def test_close_persists_actual_flat_account_and_cancels_only_owned_orders(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    clock[0] = 600
    result = ctl.tick(CLOSE, 600)
    state = store.read("runtime")[0]
    assert ex.quantity == 0 and not ex.orders and not result["snapshot"]["orders"]
    assert state["stop"] is None and state["account"]["stop"] is None
    assert D(state["account"]["quantity"]) == 0 and state["account"]["opened_at"] is None


@pytest.mark.parametrize("kwargs", [{"allow_increase": False}, {"observed_mark": "99"}])
def test_disallowed_or_price_drift_increase_does_not_open(tmp_path, kwargs):
    clock, store, ex, ctl = setup(tmp_path)
    result = ctl.tick(OPEN, 300, **kwargs)
    assert result["status"] == "checked" and ex.quantity == 0 and not ex.sent
    assert result["plan"]["reason"] == "increase_blocked"


def test_stop_fill_during_llm_wait_discards_old_quantity_response(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    qty, stop = ex.quantity, deepcopy(ex.orders[0])
    ex.quantity, ex.orders = D(0), []
    fill = {"coin": "BTC", "oid": stop["oid"], "side": "A", "sz": str(qty), "time": 599000, "tid": 1}
    ex.fills = lambda since: [fill] if since <= 599 else []
    clock[0] = 600
    assert ctl.maintain()["status"] == "checked"
    sends = len(ex.sent)
    result = ctl.tick(OPEN, 600, expected_quantity=qty)
    assert result["status"] == "stale_position" and ex.quantity == 0 and len(ex.sent) == sends
    assert not store.read("runtime")[0]["manual_pause"]


def test_fill_between_snapshot_and_history_is_not_consumed_against_stale_position(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    qty, stop = ex.quantity, deepcopy(ex.orders[0])
    fills = []
    def history(since):
        if not fills:
            ex.quantity, ex.orders = D(0), []
            fills.append({"coin": "BTC", "oid": stop["oid"], "side": "A", "sz": str(qty),
                          "time": 315000, "tid": 10})
        return fills
    ex.fills = history
    clock[0] = 315
    first = ctl.maintain()
    assert first["status"] == "checked" and D(first["snapshot"]["quantity"]) == 0
    clock[0] = 330
    second = ctl.maintain()
    assert second["status"] == "checked" and not second["manual_pause"]
    assert D(store.read("runtime")[0]["last_quantity"]) == 0


def test_unknown_initial_position_is_paused_without_adoption_or_liquidation(tmp_path):
    ex = Exchange()
    ex.quantity = D(2)
    clock, store, ex, ctl = setup(tmp_path, ex)
    for _ in range(2):
        result = ctl.maintain()
        assert result["status"] == "paused" and result["manual_pause"]
        assert not result["position_owned"] and ex.quantity == 2 and not ex.sent
    assert ctl.tick(CLOSE, 300)["status"] == "paused"
    assert ex.quantity == 2 and not ex.sent


def test_external_quantity_change_is_not_treated_as_own_risk(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ctl.tick(OPEN, 300)
    ex.quantity += 1
    sends, qty = len(ex.sent), ex.quantity
    clock[0] = 600
    result = ctl.tick(CLOSE, 600)
    assert result["status"] == "paused" and not result["position_owned"]
    assert ex.quantity == qty and len(ex.sent) == sends
    assert result["pause_reason"] == "unexplained_position_change"


def test_external_open_order_is_not_canceled_or_accepted(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ex.orders = [{"coin": "ETH", "oid": 999, "reduceOnly": False}]
    result = ctl.tick(OPEN, 300)
    assert result["status"] == "paused" and not ex.sent and ex.orders[0]["oid"] == 999


def test_authoritative_resting_response_records_oid_when_lookup_lags(tmp_path):
    ex = Exchange()
    original = ex.order
    def order(*args, **kwargs):
        original(*args, **kwargs)
        kind = "resting" if kwargs.get("stop") is not None else "filled"
        return {"status": "ok", "response": {"type": "order", "data": {"statuses": [{kind: {"oid": len(ex.sent)}}]}}}
    ex.order = order
    ex.lookup = lambda key: {"status": "unknownOid"}
    clock, store, ex, ctl = setup(tmp_path, ex)
    assert ctl.tick(OPEN, 300)["status"] == "checked"
    clock[0] = 600
    assert ctl.tick(HOLD, 600)["status"] == "checked"
    assert not store.read("runtime")[0]["pending_intents"]


def test_rejected_entry_is_not_reported_as_checked(tmp_path):
    clock, store, ex, ctl = setup(tmp_path)
    ex.order = lambda *a, **kw: {"status": "ok", "response": {"data": {"statuses": [{"error": "rejected"}]}}}
    result = ctl.tick(OPEN, 300)
    assert result["status"] == "order_rejected" and ex.quantity == 0
    assert store.read("runtime")[0]["last_action_status"] == "order_rejected"
    assert not store.read("runtime")[0]["pending_intents"]


def test_partial_entry_records_actual_quantity_and_protects_only_the_fill(tmp_path):
    ex = Exchange()
    original = ex.order
    def order(delta, *args, **kwargs):
        if kwargs.get("stop") is None and not kwargs.get("reduce_only"):
            delta /= 2
        return original(delta, *args, **kwargs)
    ex.order = order
    clock, store, ex, ctl = setup(tmp_path, ex)
    result = ctl.tick(OPEN, 300)
    assert result["status"] == "target_not_reached" and ex.quantity == D("2.5")
    assert D(store.read("runtime")[0]["account"]["quantity"]) == ex.quantity
    assert coverage(result["snapshot"])[0] == ex.quantity


@pytest.mark.parametrize("mode,expected,available", [("unifiedAccount", "2700", "2600"), ("default", "100", "90")])
def test_adapter_uses_unified_spot_collateral(mode, expected, available):
    adapter = Adapter.__new__(Adapter)
    adapter.address = "test-account"
    adapter.info = SimpleNamespace(
        user_state=lambda _: {"assetPositions": [], "marginSummary": {"accountValue": "100"}, "withdrawable": "90"},
        meta_and_asset_ctxs=lambda: ({"universe": [{"name": "BTC", "szDecimals": 5}]}, [{"markPx": "60000"}]),
        query_user_abstraction_state=lambda _: mode,
        spot_user_state=lambda _: {"balances": [{"coin": "USDC", "total": "2700", "hold": "10"}],
                                    "tokenToAvailableAfterMaintenance": [[0, "2600"]]},
        frontend_open_orders=lambda _: [],
    )
    result = adapter.snapshot()
    assert result["equity"] == expected and result["available_collateral"] == available
    assert result["account_mode"] == mode


def test_leverage_limit_and_funding_endpoint_are_explicit():
    adapter = Adapter.__new__(Adapter)
    calls = []
    adapter.exchange = SimpleNamespace(update_leverage=lambda *args: (calls.append(args), {"status": "ok"})[1])
    adapter.info = SimpleNamespace(user_funding_history=lambda *args: (calls.append(args), [])[1])
    adapter.address = "test-account"
    adapter.configure_leverage(3, is_cross=False)
    assert calls == [(3, "BTC", False)]
    with pytest.raises(ValueError):
        adapter.configure_leverage(4)
    adapter.funding(123)
    assert calls[-1] == ("test-account", 123000)
