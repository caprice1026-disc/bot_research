"""Adapter around the official Hyperliquid Python SDK."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import TESTNET_API_URL

from .connection import ValidatedConfig


_TIMEOUT_SECONDS = 15.0


def derive_signer_address(private_key: str) -> str:
    return Account.from_key(private_key).address


class HyperliquidSdkGateway:
    api_url = TESTNET_API_URL

    def __init__(
        self,
        config: ValidatedConfig,
        *,
        info_factory: Callable[..., Any] = Info,
        exchange_factory: Callable[..., Any] = Exchange,
    ) -> None:
        self._config = config
        self._wallet = Account.from_key(config.private_key)
        self._info_factory = info_factory
        self._exchange_factory = exchange_factory
        self._info: Any | None = None
        self._exchange: Any | None = None

    def _info_client(self) -> Any:
        if self._info is None:
            self._info = self._info_factory(
                TESTNET_API_URL,
                skip_ws=True,
                timeout=_TIMEOUT_SECONDS,
            )
        return self._info

    def _exchange_client(self) -> Any:
        if self._exchange is None:
            self._exchange = self._exchange_factory(
                self._wallet,
                TESTNET_API_URL,
                account_address=self._config.wallet_address,
                timeout=_TIMEOUT_SECONDS,
            )
        return self._exchange

    def get_meta(self) -> dict:
        return self._info_client().meta()

    def get_user_state(self, address: str) -> dict:
        return self._info_client().user_state(address)

    def get_user_role(self, address: str) -> dict:
        return self._info_client().user_role(address)

    def send_noop(self, nonce: int) -> dict:
        return self._exchange_client().noop(nonce)
