from __future__ import annotations

from decimal import Decimal

from hyperliquid_ai_trader.exchange.hyperliquid import HyperliquidAdapter, make_cloids
from hyperliquid_ai_trader.models import OrderPlan, Side, TradeDecision


class FakeExchangeClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] | None = None
        self.grouping: str | None = None
        self.usd_transfer_request: tuple[float, bool] | None = None

    def bulk_orders(self, requests: list[dict], grouping: str) -> dict:
        self.requests = requests
        self.grouping = grouping
        return self.response

    def usd_class_transfer(self, amount: float, to_perp: bool) -> dict:
        self.usd_transfer_request = (amount, to_perp)
        return {"status": "ok"}


class FakeInfoClient:
    def meta_and_asset_ctxs(self) -> list:
        return [
            {"universe": [{"name": "BTC", "szDecimals": 3}]},
            [{"markPx": "50010", "oraclePx": "50000", "funding": "0.00001", "openInterest": "42"}],
        ]

    def candles_snapshot(self, coin: str, interval: str, start: int, end: int) -> list[dict]:
        assert (coin, interval, end) == ("BTC", "1m", 7_300_000)
        return [
            {
                "t": index * 60_000,
                "o": str(100 + index),
                "h": str(102 + index),
                "l": str(99 + index),
                "c": str(101 + index),
                "v": str(10 + index),
            }
            for index in range(121)
        ]

    def l2_snapshot(self, coin: str) -> dict:
        assert coin == "BTC"
        return {
            "levels": [
                [{"px": "50000", "sz": "2"}],
                [{"px": "50020", "sz": "1"}],
            ]
        }

    def user_state(self, address: str) -> dict:
        assert address == "0xabc"
        return {
            "marginSummary": {"accountValue": "1000"},
            "withdrawable": "900",
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.005", "entryPx": "50000", "unrealizedPnl": "1.25"}}
            ],
        }

    def query_user_abstraction_state(self, address: str) -> str:
        assert address == "0xabc"
        return "default"

    def frontend_open_orders(self, address: str) -> list[dict]:
        assert address == "0xabc"
        return [{"coin": "BTC", "cloid": "0x" + "f" * 32, "oid": 9}]

    def spot_user_state(self, address: str) -> dict:
        assert address == "0xabc"
        return {"balances": [{"coin": "USDC", "total": "2689.31777457"}]}

    def user_fills_by_time(self, address: str, start: int, end: int, aggregate_by_time: bool) -> list[dict]:
        assert (address, start, end, aggregate_by_time) == ("0xabc", 1_000, 2_000, True)
        return [{"coin": "BTC", "tid": 7, "time": 1_500}]

    def user_funding_history(self, address: str, start: int, end: int) -> list[dict]:
        assert (address, start, end) == ("0xabc", 1_000, 2_000)
        return [{"time": 1_500, "delta": {"usdc": "0.1"}}]


def _plan(side: Side = Side.LONG) -> OrderPlan:
    decision = TradeDecision(
        side=side,
        stop_loss_pct=Decimal("0.5"),
        take_profit_pct=Decimal("1.0"),
        confidence=Decimal("0.8"),
        thesis="literal test thesis",
        would_abstain=False,
        abstain_reason=None,
    )
    return OrderPlan(
        side=side,
        size=Decimal("0.005"),
        notional=Decimal("250"),
        entry_price=Decimal("50000"),
        stop_loss_price=Decimal("49750") if side is Side.LONG else Decimal("50250"),
        take_profit_price=Decimal("50500") if side is Side.LONG else Decimal("49500"),
        risk_budget_usd=Decimal("10"),
        decision=decision,
    )


def _successful_response(total_size: str = "0.005") -> dict:
    return {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {
                "statuses": [
                    {"filled": {"totalSz": total_size, "avgPx": "50000", "oid": 1}},
                    {"resting": {"oid": 2}},
                    {"resting": {"oid": 3}},
                ]
            },
        },
    }


def test_long_bracket_is_one_ioc_normal_tpsl_action() -> None:
    client = FakeExchangeClient(_successful_response())
    adapter = HyperliquidAdapter(info_client=object(), exchange_client=client, account_address="0xabc")

    result = adapter.place_bracket(
        coin="BTC",
        plan=_plan(Side.LONG),
        run_id="run-001",
        slot=7,
        max_entry_slippage_bps=Decimal("50"),
    )

    assert result.success is True
    assert result.requires_recovery is False
    assert client.grouping == "normalTpsl"
    assert client.requests is not None
    entry, take_profit, stop_loss = client.requests
    assert entry["is_buy"] is True
    assert entry["limit_px"] == 50250.0
    assert entry["order_type"] == {"limit": {"tif": "Ioc"}}
    assert entry["reduce_only"] is False
    assert take_profit["is_buy"] is False
    assert take_profit["order_type"]["trigger"]["tpsl"] == "tp"
    assert take_profit["reduce_only"] is True
    assert stop_loss["is_buy"] is False
    assert stop_loss["order_type"]["trigger"]["tpsl"] == "sl"
    assert stop_loss["reduce_only"] is True
    assert len({request["cloid"].to_raw() for request in client.requests}) == 3


def test_short_bracket_uses_buy_reduce_only_protection() -> None:
    client = FakeExchangeClient(_successful_response())
    adapter = HyperliquidAdapter(info_client=object(), exchange_client=client, account_address="0xabc")

    adapter.place_bracket(
        coin="BTC",
        plan=_plan(Side.SHORT),
        run_id="run-001",
        slot=8,
        max_entry_slippage_bps=Decimal("50"),
    )

    assert client.requests is not None
    entry, take_profit, stop_loss = client.requests
    assert entry["is_buy"] is False
    assert entry["limit_px"] == 49750.0
    assert take_profit["is_buy"] is True
    assert stop_loss["is_buy"] is True


def test_trigger_waiting_status_is_accepted_as_protection() -> None:
    response = _successful_response()
    response["response"]["data"]["statuses"][1] = "waitingForTrigger"
    response["response"]["data"]["statuses"][2] = "waitingForTrigger"
    client = FakeExchangeClient(response)
    adapter = HyperliquidAdapter(info_client=object(), exchange_client=client, account_address="0xabc")

    result = adapter.place_bracket(
        coin="BTC",
        plan=_plan(),
        run_id="run-001",
        slot=10,
        max_entry_slippage_bps=Decimal("50"),
    )

    assert result.success is True
    assert result.requires_recovery is False


def test_partial_entry_fill_requires_recovery() -> None:
    client = FakeExchangeClient(_successful_response(total_size="0.004"))
    adapter = HyperliquidAdapter(info_client=object(), exchange_client=client, account_address="0xabc")

    result = adapter.place_bracket(
        coin="BTC",
        plan=_plan(),
        run_id="run-001",
        slot=9,
        max_entry_slippage_bps=Decimal("50"),
    )

    assert result.success is False
    assert result.requires_recovery is True
    assert result.error_type == "partial_fill"


def test_rejected_protection_requires_recovery() -> None:
    response = _successful_response()
    response["response"]["data"]["statuses"][2] = {"error": "BadTriggerPx"}
    client = FakeExchangeClient(response)
    adapter = HyperliquidAdapter(info_client=object(), exchange_client=client, account_address="0xabc")

    result = adapter.place_bracket(
        coin="BTC",
        plan=_plan(),
        run_id="run-001",
        slot=10,
        max_entry_slippage_bps=Decimal("50"),
    )

    assert result.success is False
    assert result.requires_recovery is True
    assert result.error_type == "protection_rejected"


def test_client_order_ids_are_stable_per_slot_and_distinct_per_leg() -> None:
    first = make_cloids("run-001", 11)
    second = make_cloids("run-001", 11)

    assert first == second
    assert len(set(first.values())) == 3
    assert all(value.startswith("0x") and len(value) == 34 for value in first.values())


def test_spot_to_perp_transfer_uses_sdk_usd_class_transfer() -> None:
    client = FakeExchangeClient(_successful_response())
    adapter = HyperliquidAdapter(
        info_client=FakeInfoClient(),
        exchange_client=client,
        account_address="0xabc",
    )

    result = adapter.transfer_usd_class(Decimal("2000"), to_perp=True)

    assert result == {"status": "ok"}
    assert client.usd_transfer_request == (2000.0, True)


def test_spot_to_perp_transfer_rejects_agent_wallet_before_sdk_call() -> None:
    client = FakeExchangeClient(_successful_response())
    client.account_address = "0x" + "a" * 40
    client.wallet = type("Wallet", (), {"address": "0x" + "b" * 40})()
    adapter = HyperliquidAdapter(
        info_client=FakeInfoClient(),
        exchange_client=client,
        account_address=client.account_address,
    )

    try:
        adapter.transfer_usd_class(Decimal("2000"), to_perp=True)
    except RuntimeError as exc:
        assert "Agent Wallet" in str(exc)
    else:
        raise AssertionError("agent wallet transfer should be rejected before SDK call")

    assert client.usd_transfer_request is None


def test_market_observation_and_account_are_parsed_from_sdk_shapes() -> None:
    adapter = HyperliquidAdapter(
        info_client=FakeInfoClient(),
        exchange_client=FakeExchangeClient(_successful_response()),
        account_address="0xabc",
    )

    market = adapter.get_market_observation("BTC", now_ms=7_300_000)
    account = adapter.get_account_snapshot("BTC")

    assert market.size_decimals == 3
    assert market.mark == Decimal("50010")
    assert len(market.candles) == 121
    assert market.bids[0].price == 50000.0
    assert market.asks[0].price == 50020.0
    assert account.equity == Decimal("1000")
    assert account.spot_usdc == Decimal("2689.31777457")
    assert account.withdrawable == Decimal("900")
    assert account.position_size == Decimal("0.005")
    assert account.open_orders[0]["cloid"] == "0x" + "f" * 32
    assert adapter.get_user_fills(1_000, 2_000)[0]["tid"] == 7
    assert adapter.get_user_funding(1_000, 2_000)[0]["delta"]["usdc"] == "0.1"


def test_unified_account_uses_spot_clearinghouse_collateral_and_perp_positions() -> None:
    class UnifiedInfoClient(FakeInfoClient):
        def query_user_abstraction_state(self, address: str) -> str:
            return "unifiedAccount"

        def spot_user_state(self, address: str) -> dict:
            return {
                "balances": [{"coin": "USDC", "token": 0, "total": "2689.3", "hold": "3.3"}],
                "tokenToAvailableAfterMaintenance": [[0, "2400.0"]],
            }

    adapter = HyperliquidAdapter(
        info_client=UnifiedInfoClient(),
        exchange_client=FakeExchangeClient(_successful_response()),
        account_address="0xabc",
    )

    account = adapter.get_account_snapshot("BTC")

    assert account.account_mode == "unifiedAccount"
    assert account.collateral_source == "spotClearinghouseState"
    assert account.equity == Decimal("2689.3")
    assert account.withdrawable == Decimal("2400.0")
    assert account.spot_usdc == Decimal("2689.3")


def test_account_snapshot_flags_non_target_positions_as_unknown_exposure() -> None:
    class OtherPositionInfo(FakeInfoClient):
        def user_state(self, address: str) -> dict:
            state = super().user_state(address)
            state["assetPositions"].append(
                {"position": {"coin": "ETH", "szi": "0.25", "entryPx": "2500"}}
            )
            return state

    adapter = HyperliquidAdapter(
        info_client=OtherPositionInfo(),
        exchange_client=FakeExchangeClient(_successful_response()),
        account_address="0xabc",
    )

    assert adapter.get_account_snapshot("BTC").unknown_exposure is True
