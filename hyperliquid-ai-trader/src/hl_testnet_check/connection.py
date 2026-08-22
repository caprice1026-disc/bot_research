"""Secret-safe core logic for a Hyperliquid testnet connectivity check."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Protocol


_ETHEREUM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ConfigError(ValueError):
    """Raised when required connection settings are missing or malformed."""


@dataclass(frozen=True)
class ConnectionConfig:
    wallet_address: str
    private_key: str


@dataclass(frozen=True)
class ValidatedConfig:
    wallet_address: str
    private_key: str
    signer_address: str

    @property
    def signer_matches_target(self) -> bool:
        return self.signer_address.lower() == self.wallet_address.lower()


class SdkGateway(Protocol):
    api_url: str

    def get_meta(self) -> dict: ...

    def get_user_state(self, address: str) -> dict: ...

    def get_user_role(self, address: str) -> dict: ...

    def send_noop(self, nonce: int) -> dict: ...


def validate_config(
    config: ConnectionConfig,
    derive_signer_address: Callable[[str], str],
) -> ValidatedConfig:
    wallet_address = config.wallet_address.strip()
    private_key = config.private_key.strip()

    if not wallet_address:
        raise ConfigError("HL_test_wallet is missing")
    if not private_key:
        raise ConfigError("HL_test_wallet_private_key is missing")
    if not _ETHEREUM_ADDRESS.fullmatch(wallet_address):
        raise ConfigError("HL_test_wallet is not a valid Ethereum address")

    try:
        signer_address = derive_signer_address(private_key)
    except Exception as exc:
        raise ConfigError("HL_test_wallet_private_key could not be parsed") from exc

    if not isinstance(signer_address, str) or not _ETHEREUM_ADDRESS.fullmatch(signer_address):
        raise ConfigError("HL_test_wallet_private_key could not be parsed")

    return ValidatedConfig(
        wallet_address=wallet_address,
        private_key=private_key,
        signer_address=signer_address,
    )


def run_connection_check(
    config: ValidatedConfig,
    gateway: SdkGateway,
    nonce_factory: Callable[[], int],
) -> dict[str, object]:
    base: dict[str, object] = {
        "overall_status": "error",
        "network": "testnet",
        "api_url": gateway.api_url,
        "account": {"signer_matches_target": config.signer_matches_target},
    }

    try:
        meta = gateway.get_meta()
        universe = meta.get("universe", [])
        if not isinstance(universe, list):
            raise ValueError("unexpected meta response")
        user_state = gateway.get_user_state(config.wallet_address)
        if not isinstance(user_state, dict):
            raise ValueError("unexpected user state response")
        role_response = gateway.get_user_role(config.signer_address)
        if not isinstance(role_response, dict) or not isinstance(role_response.get("role"), str):
            raise ValueError("unexpected user role response")
    except Exception:
        base["public_api"] = {"ok": False, "error_type": "public_api_error"}
        base["signed_noop"] = {"ok": False, "skipped": True}
        return base

    base["public_api"] = {
        "ok": True,
        "market_count": len(universe),
        "user_state_ok": True,
    }

    signer_role = role_response["role"]
    if config.signer_matches_target:
        signer_authorized = signer_role == "user"
    else:
        role_data = role_response.get("data")
        linked_user = role_data.get("user") if isinstance(role_data, dict) else None
        signer_authorized = (
            signer_role == "agent"
            and isinstance(linked_user, str)
            and linked_user.lower() == config.wallet_address.lower()
        )

    base["account"] = {
        "signer_matches_target": config.signer_matches_target,
        "signer_role": signer_role,
        "signer_authorized_for_target": signer_authorized,
    }
    if not signer_authorized:
        base["signed_noop"] = {
            "ok": False,
            "skipped": True,
            "error_type": "signer_not_authorized_for_target",
        }
        return base

    try:
        response = gateway.send_noop(nonce_factory())
    except Exception:
        base["signed_noop"] = {"ok": False, "error_type": "signed_api_error"}
        return base

    response_status = response.get("status") if isinstance(response, dict) else None
    if response_status != "ok":
        base["signed_noop"] = {
            "ok": False,
            "response_status": response_status,
            "error_type": "exchange_rejected",
        }
        return base

    base["overall_status"] = "ok"
    base["signed_noop"] = {"ok": True, "response_status": "ok"}
    return base
