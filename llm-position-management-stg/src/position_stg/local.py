"""Local-only environment loading, account checks and a crash-released process lock."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import time

from .config import Config, dec


def load_environment(path=None):
    from dotenv import dotenv_values
    values = {}
    if path is not None:
        source = Path(path)
        if not source.is_file():
            raise ValueError("environment file not found")
        values.update({k: v for k, v in dotenv_values(source).items() if v is not None})
    values.update(os.environ)
    return values


def live_configuration(values):
    if values.get("HL_NETWORK", "testnet").lower() != "testnet":
        raise ValueError("local live runner is Testnet only")
    cfg = Config.from_env(values)
    if "BOT_ID" not in values:
        cfg = replace(cfg, bot_id="btc-continuous-v1-testnet")
    if not cfg.bot_id.endswith("-testnet"):
        raise ValueError("use a separate BOT_ID ending in -testnet")
    if not values.get("GEMINI_API_KEY", "").strip():
        raise ValueError("GEMINI_API_KEY is required")
    credentials = {}
    for target, alias in (("TESTNET_ACCOUNT_ADDRESS", "HL_test_wallet"),
                          ("TESTNET_PRIVATE_KEY", "HL_test_wallet_private_key")):
        primary, legacy = values.get(target, "").strip(), values.get(alias, "").strip()
        if primary and legacy and primary.lower() != legacy.lower():
            raise ValueError("conflicting Testnet credential aliases: " + target)
        credentials[target] = primary or legacy
        if not credentials[target]:
            raise ValueError(target + " is required")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", credentials["TESTNET_ACCOUNT_ADDRESS"]):
        raise ValueError("invalid Testnet account address")
    return cfg, credentials


@contextmanager
def process_lock(database):
    """Exclusive kernel lock released on process termination; file contents are not authority."""
    lock_path = Path(str(Path(database).resolve()) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise RuntimeError("another local runner holds this database") from None
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def account_preflight(exchange, credentials, store):
    from eth_account import Account
    try:
        signer = Account.from_key(credentials["TESTNET_PRIVATE_KEY"]).address
    except Exception:
        raise ValueError("invalid Testnet private key") from None
    wallet = credentials["TESTNET_ACCOUNT_ADDRESS"]
    role = exchange.info.user_role(signer)
    authorized = role.get("role") == "user" if signer.lower() == wallet.lower() else (
        role.get("role") == "agent" and
        str((role.get("data") or {}).get("user", "")).lower() == wallet.lower()
    )
    if not authorized:
        raise ValueError("signer is not authorized for the Testnet account")
    snapshot = exchange.snapshot()
    saved, _ = store.read("runtime")
    if saved is None and (dec(snapshot["quantity"]) != 0 or snapshot["orders"]):
        raise ValueError("new runtime requires a flat Testnet account without orders")
    if dec(snapshot["equity"]) <= 0:
        raise ValueError("Testnet account has no usable collateral")
    # The binding is hashed to avoid writing a wallet address into the runtime logs.
    import hashlib
    binding = hashlib.sha256(("testnet:" + wallet.lower()).encode()).hexdigest()
    old, version = store.read("local-binding")
    if old is not None and old.get("account_hash") != binding:
        raise ValueError("database belongs to a different Testnet account")
    if old is None:
        store.write("local-binding", {"account_hash": binding, "network": "testnet", "at": time.time()}, version)
    return {"status": "ok", "network": "testnet", "signer_authorized": True,
            "equity": snapshot["equity"], "quantity": snapshot["quantity"],
            "open_orders": len(snapshot["orders"])}


def run_local(cfg, credentials, values, store, *, max_cycles=None):
    from .live import LiveRunner
    from .market import Market
    from .policy import Gemini
    from .testnet import TestnetController, TestnetExchange
    exchange = TestnetExchange(credentials["TESTNET_PRIVATE_KEY"], credentials["TESTNET_ACCOUNT_ADDRESS"])
    preflight = account_preflight(exchange, credentials, store)
    # A configuration change never silently retunes leverage on an existing position.
    if dec(preflight["quantity"]) == 0:
        exchange.configure_leverage(3, is_cross=False)
    print(json.dumps({"event": "preflight", **preflight}, ensure_ascii=False), flush=True)
    controller = TestnetController(cfg, store, exchange)
    runner = LiveRunner(cfg, store, Market(base_url="https://api.hyperliquid-testnet.xyz"),
                        Gemini(cfg, store, api_key=values["GEMINI_API_KEY"]), controller)
    print(json.dumps({"event": "started", "pid": os.getpid(), "bot_id": cfg.bot_id,
                      "trader_model": cfg.trader_model, "reviewer_model": cfg.reviewer_model,
                      "decision_interval_seconds": 300, "review_interval_seconds": cfg.review_interval_seconds}), flush=True)
    return runner.run_forever(max_cycles=max_cycles)
