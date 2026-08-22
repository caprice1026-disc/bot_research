"""Secret-safe command line for Testnet preflight and bounded local runs."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Sequence
import uuid

from dotenv import dotenv_values
from eth_account import Account

from .agents import ReviewerAgent, TraderAgent
from .config import Settings
from .exchange.hyperliquid import HyperliquidAdapter, create_hyperliquid_adapter
from .gemini_gateway import GeminiGateway
from .proxy import clear_invalid_loopback_proxies
from .report import generate_report, write_report
from .runner import LocalRunner, TradingService
from .storage import SQLiteStore


class PreflightError(RuntimeError):
    """Raised when a run cannot safely start."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_env_file() -> Path:
    return _project_root().parent / ".env"


def _read_settings(env_file: Path) -> Settings:
    values = {**dotenv_values(env_file), **os.environ}
    return Settings.from_mapping(values)


def _authorized_for_wallet(
    *,
    role_response: dict[str, Any],
    signer_address: str,
    wallet_address: str,
) -> bool:
    role = role_response.get("role")
    if signer_address.lower() == wallet_address.lower():
        return role == "user"
    data = role_response.get("data")
    linked_user = data.get("user") if isinstance(data, dict) else None
    return (
        role == "agent"
        and isinstance(linked_user, str)
        and linked_user.lower() == wallet_address.lower()
    )


def preflight_check(
    *,
    settings: Settings,
    adapter: HyperliquidAdapter,
    gateway: GeminiGateway,
    signer_address: str,
    now_ms: int,
) -> dict[str, Any]:
    """Validate external dependencies without returning addresses or credentials."""

    role_response = adapter.info.user_role(signer_address)
    if not isinstance(role_response, dict) or not _authorized_for_wallet(
        role_response=role_response,
        signer_address=signer_address,
        wallet_address=settings.wallet_address,
    ):
        raise PreflightError("signer is not authorized for the configured wallet")

    for model in dict.fromkeys((settings.trader_model, settings.reviewer_model)):
        gateway.validate_model(model)

    market = adapter.get_market_observation(settings.coin, now_ms=now_ms)
    account = adapter.get_account_snapshot(settings.coin)
    account_clean = (
        account.position_size == 0
        and not account.open_orders
        and not account.unknown_exposure
    )
    if not account_clean:
        raise PreflightError("account has an unknown existing position or order")

    return {
        "status": "ok",
        "network": "testnet",
        "coin": settings.coin,
        "trader_model": settings.trader_model,
        "reviewer_model": settings.reviewer_model,
        "equity": str(account.equity),
        "mark": str(market.mark),
        "size_decimals": market.size_decimals,
        "account_clean": True,
        "signer_authorized": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gemini-driven Hyperliquid Testnet trader")
    parser.add_argument("--env-file", type=Path, default=_default_env_file())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="Validate Testnet, account, signer, and Gemini models")

    dry_run = subparsers.add_parser("dry-run", help="Run accelerated no-order cycles")
    dry_run.add_argument("--cycles", type=int, default=36)
    dry_run.add_argument("--interval-seconds", type=int, default=0)

    canary = subparsers.add_parser("canary", help="Place two bounded Testnet bracket orders")
    canary.add_argument("--cycles", type=int, default=2)
    canary.add_argument("--interval-seconds", type=int, default=300)

    local = subparsers.add_parser("run-local", help="Run the bounded three-hour Testnet session")
    local.add_argument("--cycles", type=int, default=36)
    local.add_argument("--interval-seconds", type=int, default=300)

    report = subparsers.add_parser("report", help="Regenerate a secret-safe run report")
    report.add_argument("--run-id")
    return parser


def _git_sha(project_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _latest_run_id(store: SQLiteStore) -> str:
    row = store.connection.execute(
        "SELECT run_id FROM runs ORDER BY started_at_ms DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("no run is available")
    return str(row["run_id"])


def _report_prefix(project_root: Path, mode: str, run_id: str) -> Path:
    safe_run_id = "".join(character for character in run_id if character.isalnum() or character in "-_")
    return project_root / "reports" / f"{mode}-{safe_run_id}"


def _run_session(
    *,
    settings: Settings,
    adapter: HyperliquidAdapter,
    gateway: GeminiGateway,
    project_root: Path,
    cycles: int,
    interval_seconds: int,
    mode_name: str,
) -> dict[str, Any]:
    if cycles < 1 or interval_seconds < 0:
        raise ValueError("cycles must be positive and interval-seconds cannot be negative")

    constitution = (project_root / "prompts" / "constitution.md").read_text(encoding="utf-8")
    reviewer_prompt = (project_root / "prompts" / "reviewer.md").read_text(encoding="utf-8")
    trader = TraderAgent(
        gateway=gateway,
        model=settings.trader_model,
        temperature=settings.trader_temperature,
        constitution=constitution,
    )
    reviewer = ReviewerAgent(
        gateway=gateway,
        model=settings.reviewer_model,
        temperature=settings.reviewer_temperature,
        constitution=reviewer_prompt,
    )
    database_path = project_root / settings.database_path
    run_id = f"{mode_name}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    now_ms = int(time.time() * 1000)
    with SQLiteStore(database_path) as store:
        service = TradingService(
            settings=settings,
            exchange=adapter,
            trader=trader,
            reviewer=reviewer,
            store=store,
            run_id=run_id,
            git_sha=_git_sha(project_root),
        )
        service.initialize(now_ms=now_ms)
        if settings.execution_mode == "testnet_live":
            adapter.set_leverage(settings.coin, settings.leverage, settings.margin_mode)
        review_every = max(1, settings.review_interval_seconds // max(settings.trader_interval_seconds, 1))
        runner = LocalRunner(
            service=service,
            cycles=cycles,
            interval_seconds=interval_seconds,
            review_every_cycles=review_every,
        )
        summary = runner.run()
        if mode_name == "canary" and summary.reviews == 0:
            service.review_once(review_index=1, review_cycle=cycles, now_ms=int(time.time() * 1000))
        cleanup_ok = service.finalize(now_ms=int(time.time() * 1000))
        report = generate_report(store, run_id)
        json_path, markdown_path = write_report(report, _report_prefix(project_root, mode_name, run_id))
    return {
        "status": "ok" if cleanup_ok else "cleanup_failed",
        "run_id": run_id,
        "cycles": summary.cycles,
        "reviews": summary.reviews + (1 if mode_name == "canary" and summary.reviews == 0 else 0),
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
    }


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    clear_invalid_loopback_proxies(os.environ)
    project_root = _project_root()
    settings = _read_settings(args.env_file)

    if args.command == "report":
        with SQLiteStore(project_root / settings.database_path) as store:
            run_id = args.run_id or _latest_run_id(store)
            report = generate_report(store, run_id)
            paths = write_report(report, _report_prefix(project_root, "report", run_id))
        print(json.dumps({"status": "ok", "run_id": run_id, "paths": [str(path) for path in paths]}, indent=2))
        return 0

    adapter = create_hyperliquid_adapter(settings)
    gateway = GeminiGateway(api_key=settings.gemini_api_key)
    signer_address = Account.from_key(settings.private_key).address
    try:
        preflight = preflight_check(
            settings=settings,
            adapter=adapter,
            gateway=gateway,
            signer_address=signer_address,
            now_ms=int(time.time() * 1000),
        )
        if args.command == "preflight":
            result = preflight
        else:
            execution_mode = "dry_run" if args.command == "dry-run" else "testnet_live"
            run_settings = replace(settings, execution_mode=execution_mode)
            result = {
                "preflight": preflight,
                "run": _run_session(
                    settings=run_settings,
                    adapter=adapter,
                    gateway=gateway,
                    project_root=project_root,
                    cycles=args.cycles,
                    interval_seconds=args.interval_seconds,
                    mode_name=args.command,
                ),
            }
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
