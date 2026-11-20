"""Authenticated admin and explicit offline fixture commands."""
import argparse
import json
import os
import time
from .config import Config, dec
from .store import FirestoreStore, SQLiteStore, Lease
from .reporting import archive, daily_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", help="Local development only; omitted uses GCP ADC")
    parser.add_argument("--env-file", help="Explicit .env for run-testnet-local; never copied to SQLite")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("pause")
    commands.add_parser("resume")
    commands.add_parser("fixture")
    local_run = commands.add_parser("run-testnet-local", help="Continuous live Testnet runner with SQLite")
    local_run.add_argument("--max-cycles", type=int, help="Optional finite number of five-minute decisions")
    commands.add_parser("testnet-summary", help="Read local Testnet history; no network access")
    commands.add_parser("testnet-stop", help="Request orderly local shutdown; retain exchange stop orders")
    testnet = commands.add_parser("testnet-tick")
    testnet.add_argument("--decision", required=True, help="JSON file with one explicit test scenario")
    report = commands.add_parser("report")
    report.add_argument("--day", required=True)
    export = commands.add_parser("archive")
    export.add_argument("--bucket", required=True)
    cost = commands.add_parser("cloud-cost")
    cost.add_argument("--day", required=True)
    cost.add_argument("--usd", required=True)
    cost.add_argument("--source", choices=["estimate", "billing"], required=True)
    args = parser.parse_args()
    if args.command in {"run-testnet-local", "testnet-summary", "testnet-stop"}:
        if not args.sqlite:
            raise SystemExit("local Testnet commands require --sqlite")
        if args.command == "run-testnet-local":
            if args.max_cycles is not None and args.max_cycles < 1:
                raise SystemExit("--max-cycles must be positive")
            from .local import load_environment, live_configuration, process_lock, run_local
            try:
                values = load_environment(args.env_file)
                cfg, credentials = live_configuration(values)
                with process_lock(args.sqlite):
                    store = SQLiteStore(args.sqlite)
                    control, version = store.read("live-control")
                    store.write("live-control", {"stop_requested": False, "at": time.time()}, version)
                    run_local(cfg, credentials, values, store, max_cycles=args.max_cycles)
            except KeyboardInterrupt:
                print(json.dumps({"status": "stopped", "exchange_stops_retained": True}), flush=True)
            except Exception as exc:
                # Provider exceptions can contain HTTP details; never print their raw messages.
                print(json.dumps({"status": "startup_failed", "error_type": type(exc).__name__}), flush=True)
                raise SystemExit(1) from None
        else:
            from pathlib import Path
            if not Path(args.sqlite).is_file():
                raise SystemExit("local Testnet database does not exist")
            store = SQLiteStore(args.sqlite)
            if args.command == "testnet-summary":
                from .live import summary
                print(json.dumps(summary(store), ensure_ascii=False, indent=2))
            else:
                _, version = store.read("live-control")
                store.write("live-control", {"stop_requested": True, "at": time.time()}, version)
                print(json.dumps({"status": "stop_requested", "exchange_stops_retained": True}))
        return
    if args.env_file:
        raise SystemExit("--env-file is supported by run-testnet-local only")
    cfg = Config.from_env()
    if args.command == "testnet-tick" and not cfg.bot_id.endswith("-testnet"):
        raise SystemExit("use a separate BOT_ID ending in -testnet")
    store = SQLiteStore(args.sqlite) if args.sqlite else FirestoreStore(os.environ["GOOGLE_CLOUD_PROJECT"], cfg.bot_id)
    if args.command == "status":
        print(json.dumps(store.read("runtime")[0], indent=2))
    elif args.command in {"pause", "resume"}:
        existing, _ = store.read("runtime")
        if existing is None:
            raise SystemExit("runtime not initialized")
        lease = Lease(store, existing)
        try:
            if "account" in lease.data:
                from .testnet import from_env, coverage
                controller = from_env(cfg, store)
                snapshot = controller.exchange.snapshot()
                if args.command == "resume":
                    if not controller.reconcile_intents(lease.data):
                        raise SystemExit("unresolved orders; cannot resume")
                    protected, stops = coverage(snapshot)
                    if protected < abs(dec(snapshot["quantity"])):
                        raise SystemExit("unprotected position; cannot resume")
                    lease.data["last_quantity"] = snapshot["quantity"]
                    lease.data["last_reconciled"] = time.time()
                lease.data["manual_pause"] = args.command == "pause"
                lease.data["account"]["manual_pause"] = args.command == "pause"
                lease.save()
                store.write("admin-" + str(time.time_ns()), {"at": time.time(), "action": args.command}, 0)
                return
            # Shadow has no external position. Live resume needs independent reconciliation.
            if args.command == "resume" and any(a["partial"] for a in lease.data["accounts"].values()):
                raise SystemExit("data gap unresolved; retain partial experiment and use a new BOT_ID")
            lease.data["manual_pause"] = args.command == "pause"
            for a in lease.data["accounts"].values():
                a["manual_pause"] = args.command == "pause"
            lease.save()
            store.write("admin-" + str(time.time_ns()), {"at": time.time(), "action": args.command}, 0)
        finally:
            lease.release()
    elif args.command == "report":
        print(json.dumps(daily_summary(store, args.day, cfg), indent=2))
    elif args.command == "cloud-cost":
        value = dec(args.usd)
        if value < 0:
            raise SystemExit("negative cost")
        key = "cloud-cost-" + args.day
        _, version = store.read(key)
        store.write(key, {"at": time.time(), "usd": str(value), "source": args.source}, version)
    elif args.command == "archive":
        from google.cloud import storage
        print(json.dumps({"archived": archive(store, storage.Client().bucket(args.bucket), cfg.bot_id)}))
    elif args.command == "fixture":
        if not args.sqlite:
            raise SystemExit("fixture requires --sqlite; never writes a cloud account")
        from .fixture import run_fixture
        print(json.dumps(run_fixture(cfg, store), indent=2))
    elif args.command == "testnet-tick":
        from pathlib import Path
        from .testnet import from_env
        raw = json.loads(Path(args.decision).read_text(encoding="utf-8"))
        from .domain import parse_decision
        parse_decision(raw)
        print(json.dumps(from_env(cfg, store).tick(raw, int(time.time() // 300) * 300), indent=2))


if __name__ == "__main__":
    main()
