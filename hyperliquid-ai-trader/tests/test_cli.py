import importlib
import json
from pathlib import Path
import uuid

import pytest


PRIVATE_KEY = "0x" + "33" * 32
TARGET_ADDRESS = "0x3333333333333333333333333333333333333333"
SIGNER_ADDRESS = "0x4444444444444444444444444444444444444444"


def cli_api():
    try:
        return importlib.import_module("hl_testnet_check.cli")
    except ModuleNotFoundError:
        pytest.fail("hl_testnet_check.cli must be implemented")


class SuccessfulGateway:
    api_url = "https://api.hyperliquid-testnet.xyz"

    def get_meta(self) -> dict:
        return {
            "universe": [
                {"name": "BTC", "szDecimals": 5, "maxLeverage": 40, "onlyIsolated": False}
            ]
        }

    def get_user_state(self, address: str) -> dict:
        return {
            "marginSummary": {
                "accountValue": "0.0",
                "totalNtlPos": "0.0",
                "totalRawUsd": "0.0",
                "totalMarginUsed": "0.0",
            },
            "crossMarginSummary": {
                "accountValue": "0.0",
                "totalNtlPos": "0.0",
                "totalRawUsd": "0.0",
                "totalMarginUsed": "0.0",
            },
            "withdrawable": "0.0",
            "assetPositions": [],
            "time": 1_777_777_777_777,
        }

    def get_user_role(self, address: str) -> dict:
        return {"role": "agent", "data": {"user": TARGET_ADDRESS}}

    def send_noop(self, nonce: int) -> dict:
        return {"status": "ok", "response": {"type": "noop", "data": {"statuses": []}}}


def unique_test_directory() -> Path:
    path = Path.cwd() / f"pytest-cache-files-hl-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_run_cli_loads_env_and_writes_secret_safe_result(capsys):
    cli = cli_api()
    run_dir = unique_test_directory()
    env_file = run_dir / ".env"
    output_file = run_dir / "result.json"
    env_file.write_text(
        f"HL_test_wallet={TARGET_ADDRESS}\nHL_test_wallet_private_key={PRIVATE_KEY}\n",
        encoding="utf-8",
    )

    exit_code = cli.run_cli(
        ["--env-file", str(env_file), "--output", str(output_file)],
        derive_signer_address=lambda _: SIGNER_ADDRESS,
        gateway_factory=lambda _: SuccessfulGateway(),
        nonce_factory=lambda: 1_777_777_777_777,
    )

    captured = capsys.readouterr().out
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["overall_status"] == "ok"
    assert saved["signed_noop"]["ok"] is True
    assert PRIVATE_KEY not in captured
    assert PRIVATE_KEY not in output_file.read_text(encoding="utf-8")
    assert TARGET_ADDRESS not in captured


def test_run_cli_returns_config_error_without_echoing_secret(capsys):
    cli = cli_api()
    run_dir = unique_test_directory()
    env_file = run_dir / ".env"
    output_file = run_dir / "result.json"
    env_file.write_text(
        f"HL_test_wallet=invalid\nHL_test_wallet_private_key={PRIVATE_KEY}\n",
        encoding="utf-8",
    )

    exit_code = cli.run_cli(
        ["--env-file", str(env_file), "--output", str(output_file)],
        derive_signer_address=lambda _: SIGNER_ADDRESS,
        gateway_factory=lambda _: SuccessfulGateway(),
        nonce_factory=lambda: 1,
    )

    captured = capsys.readouterr().out
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert saved == {
        "overall_status": "error",
        "network": "testnet",
        "config": {"ok": False, "error_type": "config_error"},
    }
    assert PRIVATE_KEY not in captured
    assert PRIVATE_KEY not in output_file.read_text(encoding="utf-8")
