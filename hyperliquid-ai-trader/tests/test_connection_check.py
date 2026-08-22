import importlib
import json

import pytest


PRIVATE_KEY = "0x" + "11" * 32
TARGET_ADDRESS = "0x1111111111111111111111111111111111111111"
SIGNER_ADDRESS = "0x2222222222222222222222222222222222222222"


def connection_api():
    try:
        return importlib.import_module("hl_testnet_check.connection")
    except ModuleNotFoundError:
        pytest.fail("hl_testnet_check.connection must be implemented")


class SuccessfulGateway:
    api_url = "https://api.hyperliquid-testnet.xyz"

    def __init__(self):
        self.noop_nonces: list[int] = []

    def get_meta(self) -> dict:
        return {
            "universe": [
                {"name": "BTC", "szDecimals": 5, "maxLeverage": 40, "onlyIsolated": False},
                {"name": "ETH", "szDecimals": 4, "maxLeverage": 25, "onlyIsolated": False},
            ]
        }

    def get_user_state(self, address: str) -> dict:
        return {
            "marginSummary": {
                "accountValue": "100.0",
                "totalNtlPos": "0.0",
                "totalRawUsd": "100.0",
                "totalMarginUsed": "0.0",
            },
            "crossMarginSummary": {
                "accountValue": "100.0",
                "totalNtlPos": "0.0",
                "totalRawUsd": "100.0",
                "totalMarginUsed": "0.0",
            },
            "withdrawable": "100.0",
            "assetPositions": [],
            "time": 1_777_777_777_777,
        }

    def get_user_role(self, address: str) -> dict:
        return {"role": "agent", "data": {"user": TARGET_ADDRESS}}

    def send_noop(self, nonce: int) -> dict:
        self.noop_nonces.append(nonce)
        return {"status": "ok", "response": {"type": "noop", "data": {"statuses": []}}}


def valid_config(api):
    config = api.ConnectionConfig(wallet_address=TARGET_ADDRESS, private_key=PRIVATE_KEY)
    return api.validate_config(config, derive_signer_address=lambda _: SIGNER_ADDRESS)


def test_validate_config_rejects_missing_private_key():
    api = connection_api()
    config = api.ConnectionConfig(wallet_address=TARGET_ADDRESS, private_key="")

    with pytest.raises(api.ConfigError, match="HL_test_wallet_private_key is missing"):
        api.validate_config(config, derive_signer_address=lambda _: SIGNER_ADDRESS)


def test_validate_config_rejects_malformed_wallet_address():
    api = connection_api()
    config = api.ConnectionConfig(wallet_address="not-an-address", private_key=PRIVATE_KEY)

    with pytest.raises(api.ConfigError, match="HL_test_wallet is not a valid Ethereum address"):
        api.validate_config(config, derive_signer_address=lambda _: SIGNER_ADDRESS)


def test_validate_config_redacts_private_key_when_derivation_fails():
    api = connection_api()
    config = api.ConnectionConfig(wallet_address=TARGET_ADDRESS, private_key=PRIVATE_KEY)

    with pytest.raises(api.ConfigError) as caught:
        api.validate_config(
            config,
            derive_signer_address=lambda _: (_ for _ in ()).throw(ValueError(PRIVATE_KEY)),
        )

    assert str(caught.value) == "HL_test_wallet_private_key could not be parsed"
    assert PRIVATE_KEY not in str(caught.value)


def test_run_connection_check_reports_public_and_signed_success_without_secrets():
    api = connection_api()
    validated = valid_config(api)
    gateway = SuccessfulGateway()

    result = api.run_connection_check(validated, gateway, nonce_factory=lambda: 1_777_777_777_777)

    assert result == {
        "overall_status": "ok",
        "network": "testnet",
        "api_url": "https://api.hyperliquid-testnet.xyz",
        "account": {
            "signer_matches_target": False,
            "signer_role": "agent",
            "signer_authorized_for_target": True,
        },
        "public_api": {"ok": True, "market_count": 2, "user_state_ok": True},
        "signed_noop": {"ok": True, "response_status": "ok"},
    }
    assert gateway.noop_nonces == [1_777_777_777_777]
    assert PRIVATE_KEY not in json.dumps(result)


def test_run_connection_check_classifies_exchange_rejection():
    api = connection_api()
    validated = valid_config(api)

    class RejectedGateway(SuccessfulGateway):
        def send_noop(self, nonce: int) -> dict:
            self.noop_nonces.append(nonce)
            return {"status": "err", "response": "User or API Wallet does not exist"}

    result = api.run_connection_check(validated, RejectedGateway(), nonce_factory=lambda: 7)

    assert result["overall_status"] == "error"
    assert result["public_api"]["ok"] is True
    assert result["signed_noop"] == {
        "ok": False,
        "response_status": "err",
        "error_type": "exchange_rejected",
    }


def test_run_connection_check_skips_noop_when_signer_belongs_to_another_user():
    api = connection_api()
    validated = valid_config(api)

    class WrongUserGateway(SuccessfulGateway):
        def get_user_role(self, address: str) -> dict:
            return {
                "role": "agent",
                "data": {"user": "0x9999999999999999999999999999999999999999"},
            }

    gateway = WrongUserGateway()
    result = api.run_connection_check(validated, gateway, nonce_factory=lambda: 7)

    assert result["overall_status"] == "error"
    assert result["account"] == {
        "signer_matches_target": False,
        "signer_role": "agent",
        "signer_authorized_for_target": False,
    }
    assert result["signed_noop"] == {
        "ok": False,
        "skipped": True,
        "error_type": "signer_not_authorized_for_target",
    }
    assert gateway.noop_nonces == []


def test_run_connection_check_accepts_target_wallet_as_direct_user_signer():
    api = connection_api()
    config = api.ConnectionConfig(wallet_address=TARGET_ADDRESS, private_key=PRIVATE_KEY)
    validated = api.validate_config(config, derive_signer_address=lambda _: TARGET_ADDRESS)

    class DirectUserGateway(SuccessfulGateway):
        def get_user_role(self, address: str) -> dict:
            return {"role": "user"}

    result = api.run_connection_check(validated, DirectUserGateway(), nonce_factory=lambda: 9)

    assert result["overall_status"] == "ok"
    assert result["account"] == {
        "signer_matches_target": True,
        "signer_role": "user",
        "signer_authorized_for_target": True,
    }


def test_run_connection_check_stops_before_noop_when_public_api_fails():
    api = connection_api()
    validated = valid_config(api)

    class UnreachableGateway(SuccessfulGateway):
        def get_meta(self) -> dict:
            raise ConnectionError("proxy included a secret-like diagnostic")

    gateway = UnreachableGateway()
    result = api.run_connection_check(validated, gateway, nonce_factory=lambda: 7)

    assert result["overall_status"] == "error"
    assert result["public_api"] == {"ok": False, "error_type": "public_api_error"}
    assert result["signed_noop"] == {"ok": False, "skipped": True}
    assert gateway.noop_nonces == []
    assert "proxy" not in json.dumps(result)
