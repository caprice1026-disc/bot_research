import importlib

import pytest

from hl_testnet_check.connection import ValidatedConfig


PRIVATE_KEY = "0x" + "11" * 32
TARGET_ADDRESS = "0x1111111111111111111111111111111111111111"
EXPECTED_SIGNER = "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"


def sdk_api():
    try:
        return importlib.import_module("hl_testnet_check.sdk_gateway")
    except ModuleNotFoundError:
        pytest.fail("hl_testnet_check.sdk_gateway must be implemented")


def test_derive_signer_address_uses_the_sdk_account_parser():
    sdk = sdk_api()

    assert sdk.derive_signer_address(PRIVATE_KEY) == EXPECTED_SIGNER


def test_gateway_calls_testnet_info_and_exchange_interfaces():
    sdk = sdk_api()
    validated = ValidatedConfig(
        wallet_address=TARGET_ADDRESS,
        private_key=PRIVATE_KEY,
        signer_address=EXPECTED_SIGNER,
    )

    class FakeInfo:
        def __init__(self, base_url, *, skip_ws, timeout):
            if base_url != "https://api.hyperliquid-testnet.xyz" or skip_ws is not True or timeout != 15.0:
                raise AssertionError("Info must be configured for bounded testnet HTTP access")

        def meta(self):
            return {"universe": []}

        def user_state(self, address):
            return {"queriedAddress": address, "assetPositions": []}

        def user_role(self, address):
            return {"role": "agent", "data": {"user": TARGET_ADDRESS}}

    class FakeExchange:
        def __init__(self, wallet, base_url, *, account_address, timeout):
            if wallet.address != EXPECTED_SIGNER:
                raise AssertionError("Exchange must use the derived signer")
            if base_url != "https://api.hyperliquid-testnet.xyz":
                raise AssertionError("Exchange must use testnet")
            if account_address != TARGET_ADDRESS or timeout != 15.0:
                raise AssertionError("Exchange must retain the target account and timeout")

        def noop(self, nonce):
            return {"status": "ok", "nonceObserved": nonce}

    gateway = sdk.HyperliquidSdkGateway(
        validated,
        info_factory=FakeInfo,
        exchange_factory=FakeExchange,
    )

    assert gateway.api_url == "https://api.hyperliquid-testnet.xyz"
    assert gateway.get_meta() == {"universe": []}
    assert gateway.get_user_state(TARGET_ADDRESS) == {
        "queriedAddress": TARGET_ADDRESS,
        "assetPositions": [],
    }
    assert gateway.get_user_role(EXPECTED_SIGNER) == {
        "role": "agent",
        "data": {"user": TARGET_ADDRESS},
    }
    assert gateway.send_noop(1234) == {"status": "ok", "nonceObserved": 1234}
