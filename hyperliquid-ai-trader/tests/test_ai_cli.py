from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.cli import PreflightError, build_parser, preflight_check
from hyperliquid_ai_trader.config import Settings
from hyperliquid_ai_trader.exchange.base import ExchangeAccountSnapshot


def _settings() -> Settings:
    return Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
            "GEMINI_MODEL": "gemini-env-model",
        }
    )


class FakeInfo:
    def user_role(self, signer: str) -> dict:
        return {"role": "agent", "data": {"user": "0x" + "1" * 40}}


class FakeAdapter:
    info = FakeInfo()

    def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot:
        return ExchangeAccountSnapshot(
            equity=Decimal("1000"),
            withdrawable=Decimal("1000"),
            position_size=Decimal("0"),
            entry_price=None,
            unrealized_pnl=Decimal("0"),
            open_orders=[],
        )

    def get_market_observation(self, coin: str, *, now_ms: int):
        return type("Market", (), {"mark": Decimal("50000"), "size_decimals": 3})()


class FakeGateway:
    def __init__(self) -> None:
        self.models: list[str] = []

    def validate_model(self, model: str) -> None:
        self.models.append(model)


def test_preflight_checks_agent_link_models_and_clean_account_without_addresses() -> None:
    gateway = FakeGateway()

    result = preflight_check(
        settings=_settings(),
        adapter=FakeAdapter(),
        gateway=gateway,
        signer_address="0x" + "2" * 40,
        now_ms=1_000,
    )

    assert result == {
        "status": "ok",
        "network": "testnet",
        "coin": "BTC",
        "trader_model": "gemini-env-model",
        "reviewer_model": "gemini-env-model",
        "equity": "1000",
        "spot_usdc": "0",
        "available_collateral": "1000",
        "account_mode": "default",
        "collateral_source": "perpClearinghouseState",
        "mark": "50000",
        "size_decimals": 3,
        "account_clean": True,
        "signer_authorized": True,
    }
    assert gateway.models == ["gemini-env-model"]
    assert "0x" not in str(result)


def test_preflight_rejects_unexpected_account_mode() -> None:
    settings = Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
            "HL_EXPECTED_ACCOUNT_MODE": "unifiedAccount",
        }
    )

    with pytest.raises(PreflightError, match="account mode"):
        preflight_check(
            settings=settings,
            adapter=FakeAdapter(),
            gateway=FakeGateway(),
            signer_address="0x" + "2" * 40,
            now_ms=1_000,
        )


def test_preflight_explains_spot_funds_when_perp_equity_is_zero() -> None:
    class SpotFundedAdapter(FakeAdapter):
        def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot:
            snapshot = super().get_account_snapshot(coin)
            return ExchangeAccountSnapshot(
                equity=Decimal("0"),
                withdrawable=Decimal("0"),
                position_size=snapshot.position_size,
                entry_price=snapshot.entry_price,
                unrealized_pnl=snapshot.unrealized_pnl,
                open_orders=snapshot.open_orders,
                unknown_exposure=snapshot.unknown_exposure,
                spot_usdc=Decimal("2689.31777457"),
            )

    with pytest.raises(PreflightError, match="Spot USDC.*Perp"):
        preflight_check(
            settings=_settings(),
            adapter=SpotFundedAdapter(),
            gateway=FakeGateway(),
            signer_address="0x" + "2" * 40,
            now_ms=1_000,
        )


def test_preflight_rejects_unlinked_agent() -> None:
    class UnlinkedInfo:
        def user_role(self, signer: str) -> dict:
            return {"role": "agent", "data": {"user": "0x" + "9" * 40}}

    adapter = FakeAdapter()
    adapter.info = UnlinkedInfo()

    with pytest.raises(PreflightError, match="authorized"):
        preflight_check(
            settings=_settings(),
            adapter=adapter,
            gateway=FakeGateway(),
            signer_address="0x" + "2" * 40,
            now_ms=1_000,
        )


def test_preflight_rejects_unknown_non_target_exposure() -> None:
    class UnknownExposureAdapter(FakeAdapter):
        def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot:
            snapshot = super().get_account_snapshot(coin)
            return ExchangeAccountSnapshot(
                equity=snapshot.equity,
                withdrawable=snapshot.withdrawable,
                position_size=snapshot.position_size,
                entry_price=snapshot.entry_price,
                unrealized_pnl=snapshot.unrealized_pnl,
                open_orders=snapshot.open_orders,
                unknown_exposure=True,
            )

    with pytest.raises(PreflightError, match="unknown"):
        preflight_check(
            settings=_settings(),
            adapter=UnknownExposureAdapter(),
            gateway=FakeGateway(),
            signer_address="0x" + "2" * 40,
            now_ms=1_000,
        )


def test_parser_exposes_preflight_dry_run_canary_local_and_report_commands() -> None:
    parser = build_parser()

    for command in ("preflight", "dry-run", "canary", "run-local", "report"):
        args = parser.parse_args([command])
        assert args.command == command

    transfer = parser.parse_args(["transfer-to-perp", "--amount", "2000"])
    assert transfer.command == "transfer-to-perp"
    assert transfer.amount == Decimal("2000")
