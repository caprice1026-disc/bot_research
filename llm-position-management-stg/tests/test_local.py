"""Local operation boundaries: secrets, network, ownership and one process per DB."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from position_stg.local import load_environment, live_configuration, process_lock, account_preflight
from position_stg.market import Market
from position_stg.store import SQLiteStore


def values():
    return {"GEMINI_API_KEY": "fixture-only", "HL_test_wallet": "0x" + "11" * 20,
            "HL_test_wallet_private_key": "0x" + "22" * 32,
            "TRADER_MODEL": "gemini-3.6-flash", "REVIEWER_MODEL": "gemini-3.8-flash"}


def test_explicit_environment_load_and_testnet_aliases(tmp_path, monkeypatch):
    for name in values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BOT_ID", raising=False)
    monkeypatch.delenv("HL_NETWORK", raising=False)
    source = tmp_path / ".env"
    source.write_text("\n".join(k + "=" + v for k, v in values().items()), encoding="utf-8")
    cfg, credentials = live_configuration(load_environment(source))
    assert cfg.bot_id.endswith("-testnet")
    assert cfg.trader_model == "gemini-3.6-flash"
    assert cfg.reviewer_model == "gemini-3.8-flash"
    assert credentials["TESTNET_ACCOUNT_ADDRESS"] == values()["HL_test_wallet"]
    assert "GEMINI_API_KEY" not in os.environ
    assert "fixture-only" not in json.dumps(cfg.public())
    with pytest.raises(ValueError, match="Testnet only"):
        live_configuration({**values(), "HL_NETWORK": "mainnet"})
    with pytest.raises(ValueError, match="conflicting"):
        live_configuration({**values(), "TESTNET_ACCOUNT_ADDRESS": "0x" + "33" * 20})


def test_process_lock_excludes_another_process_and_releases(tmp_path):
    database = tmp_path / "run.db"
    code = (
        "from position_stg.local import process_lock\n"
        "import sys\n"
        "with process_lock(sys.argv[1]): print('locked')\n"
    )
    with process_lock(database):
        blocked = subprocess.run([sys.executable, "-B", "-c", code, str(database)],
                                 capture_output=True, text=True, timeout=15)
        assert blocked.returncode != 0
        assert "another local runner" in blocked.stderr
    allowed = subprocess.run([sys.executable, "-B", "-c", code, str(database)],
                             capture_output=True, text=True, timeout=15)
    assert allowed.returncode == 0 and "locked" in allowed.stdout


def test_public_market_uses_selected_testnet_endpoint_only():
    calls = []
    class Session:
        def post(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
    market = Market(session=Session(), base_url="https://api.hyperliquid-testnet.xyz")
    market.info({"type": "meta"})
    assert calls == ["https://api.hyperliquid-testnet.xyz/info"]
    with pytest.raises(ValueError):
        Market(base_url="https://example.invalid")


def test_preflight_rejects_existing_positions_and_database_rebinding(tmp_path):
    creds = {"TESTNET_PRIVATE_KEY": values()["HL_test_wallet_private_key"],
             "TESTNET_ACCOUNT_ADDRESS": values()["HL_test_wallet"]}
    class Exchange:
        quantity = "0"
        info = SimpleNamespace(user_role=lambda signer: {"role": "agent", "data": {"user": creds["TESTNET_ACCOUNT_ADDRESS"]}})
        def snapshot(self):
            return {"quantity": self.quantity, "orders": [], "equity": "500"}
    exchange = Exchange()
    store = SQLiteStore(tmp_path / "state.db")
    exchange.quantity = "1"
    with pytest.raises(ValueError, match="flat"):
        account_preflight(exchange, creds, store)
    exchange.quantity = "0"
    assert account_preflight(exchange, creds, store)["signer_authorized"]
    other = {**creds, "TESTNET_ACCOUNT_ADDRESS": "0x" + "33" * 20}
    exchange.info = SimpleNamespace(user_role=lambda signer: {"role": "agent", "data": {"user": other["TESTNET_ACCOUNT_ADDRESS"]}})
    with pytest.raises(ValueError, match="different"):
        account_preflight(exchange, other, store)


def test_sqlite_prefix_reads_and_wal(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.write("live-cycle-1", {"x": 1}, 0)
    store.write("llm-trader-1", {"x": 2}, 0)
    assert [key for key, _, _ in store.items("live-cycle-")] == ["live-cycle-1"]
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_summary_and_stop_cli_need_no_credentials_or_network(tmp_path, monkeypatch, capsys):
    from position_stg.cli import main
    path = tmp_path / "runtime.db"
    store = SQLiteStore(path)
    monkeypatch.setattr(sys, "argv", ["position-stg", "--sqlite", str(path), "testnet-summary"])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "not_started"
    monkeypatch.setattr(sys, "argv", ["position-stg", "--sqlite", str(path), "testnet-stop"])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "stop_requested"
    assert store.read("live-control")[0]["stop_requested"] is True


def test_live_cli_rejects_mainnet_without_exposing_secrets(tmp_path, monkeypatch, capsys):
    from position_stg.cli import main
    source = tmp_path / ".env"
    source.write_text("GEMINI_API_KEY=do-not-print-fixture-secret\nHL_NETWORK=mainnet\n", encoding="utf-8")
    monkeypatch.delenv("HL_NETWORK", raising=False)
    monkeypatch.setattr(sys, "argv", ["position-stg", "--sqlite", str(tmp_path / "never.db"),
                                     "--env-file", str(source), "run-testnet-local"])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    output = capsys.readouterr().out
    assert "do-not-print-fixture-secret" not in output
    assert json.loads(output)["status"] == "startup_failed"
