"""Offline fixture CLI.  It deliberately has no live exchange or Gemini mode."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, RiskLimits
from trading_core.market_data.models import MarketTick

from .policy import ScriptedPolicy
from .report import build_report, render_report_markdown
from .runner import PositionRunner, RunnerConfig
from .store import RunStore


class ConfigError(ValueError):
    """Raised when a local fixture config cannot be replayed safely."""


def _decimal(value: Any, *, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ConfigError(f"{name} must be a decimal") from error
    if not parsed.is_finite():
        raise ConfigError(f"{name} must be finite")
    return parsed


def _object(payload: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError(f"{name} must be an object")
    return payload


def _account(payload: dict[str, Any]) -> AccountSnapshot:
    quantity = _decimal(payload.get("signed_quantity", "0"), name="initial_account.signed_quantity")
    average = payload.get("average_entry_price")
    return AccountSnapshot(
        position_version=int(payload.get("position_version", 0)),
        signed_quantity=quantity,
        average_entry_price=_decimal(average, name="initial_account.average_entry_price") if average is not None else None,
        cash=_decimal(payload["cash"], name="initial_account.cash"),
        equity=_decimal(payload["equity"], name="initial_account.equity"),
        unrealized_pnl=_decimal(payload.get("unrealized_pnl", "0"), name="initial_account.unrealized_pnl"),
        opened_at_ms=payload.get("opened_at_ms"),
        stop_price=_decimal(payload["stop_price"], name="initial_account.stop_price") if payload.get("stop_price") is not None else None,
        pending_order_ids=tuple(payload.get("pending_order_ids", ())),
        day_start_equity=_decimal(payload.get("day_start_equity", payload["equity"]), name="initial_account.day_start_equity"),
        daily_realized_pnl=_decimal(payload.get("daily_realized_pnl", "0"), name="initial_account.daily_realized_pnl"),
        peak_equity=_decimal(payload.get("peak_equity", payload["equity"]), name="initial_account.peak_equity"),
    )


def _limits(payload: dict[str, Any]) -> RiskLimits:
    return RiskLimits(
        exposure_anchor_usd=_decimal(payload["exposure_anchor_usd"], name="limits.exposure_anchor_usd"),
        max_position_notional_usd=_decimal(payload["max_position_notional_usd"], name="limits.max_position_notional_usd"),
        min_notional_usd=_decimal(payload["min_notional_usd"], name="limits.min_notional_usd"),
        risk_per_position_pct=_decimal(payload["risk_per_position_pct"], name="limits.risk_per_position_pct"),
        max_daily_loss_pct=_decimal(payload["max_daily_loss_pct"], name="limits.max_daily_loss_pct"),
        max_drawdown_pct=_decimal(payload["max_drawdown_pct"], name="limits.max_drawdown_pct"),
        max_hold_ms=int(payload["max_hold_ms"]),
    )


def _costs(payload: dict[str, Any]) -> ExecutionCosts:
    return ExecutionCosts(
        fee_rate=_decimal(payload["fee_rate"], name="costs.fee_rate"),
        spread_bps=_decimal(payload["spread_bps"], name="costs.spread_bps"),
        slippage_bps=_decimal(payload["slippage_bps"], name="costs.slippage_bps"),
    )


def _tick(payload: dict[str, Any]) -> MarketTick:
    return MarketTick(
        timestamp_ms=int(payload["timestamp_ms"]),
        open_price=_decimal(payload["open_price"], name="tick.open_price"),
        high_price=_decimal(payload["high_price"], name="tick.high_price"),
        low_price=_decimal(payload["low_price"], name="tick.low_price"),
        close_price=_decimal(payload["close_price"], name="tick.close_price"),
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def replay(config_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ConfigError(f"refusing to overwrite existing output: {output}")
    try:
        raw = config_path.read_bytes()
        config = _object(json.loads(raw), name="config")
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read JSON config: {config_path}") from error
    if config.get("schema_version") != 1:
        raise ConfigError("config.schema_version must be 1")
    decisions = _object(config.get("decisions"), name="decisions")
    temporary = output.with_name(f".{output.name}.tmp-{uuid4().hex}")
    temporary.mkdir(parents=True)
    runner = PositionRunner(
        initial=_account(_object(config.get("initial_account"), name="initial_account")),
        limits=_limits(_object(config.get("limits"), name="limits")),
        costs=_costs(_object(config.get("costs"), name="costs")),
        config=RunnerConfig(
            run_id=str(config["run_id"]),
            decision_interval_ms=int(config["decision_interval_ms"]),
            max_response_age_ms=int(config["max_response_age_ms"]),
            max_model_cost_usd=_decimal(config["max_model_cost_usd"], name="max_model_cost_usd"),
        ),
        policy=ScriptedPolicy(decisions),
        store=RunStore(temporary / "run.db"),
        config_fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    ticks_raw = config.get("ticks")
    if not isinstance(ticks_raw, list):
        raise ConfigError("ticks must be a list")
    result = runner.run([_tick(_object(value, name="tick")) for value in ticks_raw])
    report = build_report(result)
    try:
        _write_json(
            temporary / "run_manifest.json",
            {
                "schema_version": 1,
                "status": "complete",
                "run_id": config["run_id"],
                "config_sha256": hashlib.sha256(raw).hexdigest(),
                "mode": "offline_scripted_replay",
            },
        )
        _write_jsonl(temporary / "observations.jsonl", [record.observation for record in result.records])
        _write_jsonl(temporary / "decisions.jsonl", list(result.records))
        _write_jsonl(temporary / "fills.jsonl", [event for record in result.records for event in record.events if event.kind == "fill"])
        _write_jsonl(temporary / "equity.jsonl", [result.final_snapshot])
        _write_json(temporary / "report.json", report)
        (temporary / "report.md").write_text(render_report_markdown(report), encoding="utf-8", newline="\n")
        temporary.replace(output)
    except Exception:
        # ponytail: retain an incomplete directory for inspection; add cleanup only when retries need it.
        raise
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-position-management")
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="run an offline scripted fixture")
    replay_parser.add_argument("--config", required=True, type=Path)
    replay_parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "replay":
            print(json.dumps(replay(args.config, args.output), ensure_ascii=False, sort_keys=True))
            return 0
    except (ConfigError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
