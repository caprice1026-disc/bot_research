"""Command-line entry point for the Hyperliquid testnet connection check."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
from pathlib import Path
import time

from dotenv import dotenv_values

from .connection import ConfigError, ConnectionConfig, SdkGateway, run_connection_check, validate_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Hyperliquid testnet public API and signed noop access."
    )
    parser.add_argument("--env-file", type=Path, required=True, help="Path to the .env file")
    parser.add_argument("--output", type=Path, required=True, help="Path for secret-safe JSON evidence")
    return parser


def _write_and_print(result: dict[str, object], output_path: Path) -> None:
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    derive_signer_address: Callable[[str], str] | None = None,
    gateway_factory: Callable[[object], SdkGateway] | None = None,
    nonce_factory: Callable[[], int] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    values = dotenv_values(args.env_file)
    config = ConnectionConfig(
        wallet_address=values.get("HL_test_wallet") or "",
        private_key=values.get("HL_test_wallet_private_key") or "",
    )

    if derive_signer_address is None or gateway_factory is None:
        from .sdk_gateway import HyperliquidSdkGateway, derive_signer_address as sdk_derive

        derive_signer_address = derive_signer_address or sdk_derive
        gateway_factory = gateway_factory or HyperliquidSdkGateway

    nonce_factory = nonce_factory or (lambda: int(time.time() * 1000))

    try:
        validated = validate_config(config, derive_signer_address)
    except ConfigError:
        result: dict[str, object] = {
            "overall_status": "error",
            "network": "testnet",
            "config": {"ok": False, "error_type": "config_error"},
        }
        _write_and_print(result, args.output)
        return 2

    try:
        gateway = gateway_factory(validated)
    except Exception:
        result = {
            "overall_status": "error",
            "network": "testnet",
            "public_api": {"ok": False, "error_type": "sdk_setup_error"},
            "signed_noop": {"ok": False, "skipped": True},
        }
        _write_and_print(result, args.output)
        return 1

    result = run_connection_check(validated, gateway, nonce_factory)
    _write_and_print(result, args.output)
    return 0 if result["overall_status"] == "ok" else 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
