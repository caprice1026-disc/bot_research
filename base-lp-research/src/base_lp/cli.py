from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from base_lp.backtest.benchmarks import hodl_terminal_value
from base_lp.backtest.engine import SimulationConfig, price_token1_per_token0, run_backtest
from base_lp.config import ResearchConfig, load_config
from base_lp.data.ingest import iter_log_chunks, normalize_event_rows, timestamp_to_block
from base_lp.data.manifest import build_manifest, write_manifest
from base_lp.data.normalize import log_record_from_rpc
from base_lp.data.persist import append_jsonl, read_parquet_rows, sha256_file, write_jsonl, write_parquet
from base_lp.data.pool import resolve_pool
from base_lp.data.rpc import JsonRpcClient
from base_lp.data.validate import validate_log_order
from base_lp.experiments.runner import run_parameter_sweep
from base_lp.experiments.walk_forward import assess_walk_forward
from base_lp.reporting import write_json, write_report
from base_lp.schemas import LogRecord, PoolMetadata, SwapEvent
from base_lp.strategies.params import half_width_ticks_for_pct
from base_lp.strategies.static import StaticRangeStrategy
from base_lp.strategies.threshold_reset import ThresholdResetStrategy


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "base_weth_usdc_005.yaml"


def _client(
    config: ResearchConfig,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> JsonRpcClient:
    url = os.environ.get(config.rpc_env)
    if not url:
        raise RuntimeError(f"missing required environment variable: {config.rpc_env}")
    return JsonRpcClient(
        url,
        timeout_seconds=config.rpc_timeout_seconds if timeout_seconds is None else timeout_seconds,
        max_retries=config.rpc_max_retries if max_retries is None else max_retries,
    )


def _parse_utc(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _pool_path(root: Path) -> Path:
    return root / "results" / "pool_metadata.json"


def _load_pool(path: Path) -> PoolMetadata:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PoolMetadata(**raw)


def _checkpoint_path(root: Path) -> Path:
    return root / "results" / "collection_checkpoint.json"


def _new_checkpoint(
    chain_id: int,
    pool_address: str,
    block_start: int,
    block_end: int,
    chunk_size: int,
    pilot_start_utc: str | None = None,
    pilot_end_utc: str | None = None,
) -> dict[str, Any]:
    checkpoint = {
        "status": "running",
        "chain_id": chain_id,
        "pool_address": pool_address.lower(),
        "block_start": block_start,
        "block_end": block_end,
        "chunk_size": chunk_size,
        "next_block": block_start,
        "chunks_completed": 0,
        "logs_collected": 0,
    }
    if pilot_start_utc is not None:
        checkpoint["pilot_start_utc"] = pilot_start_utc
    if pilot_end_utc is not None:
        checkpoint["pilot_end_utc"] = pilot_end_utc
    return checkpoint


def _checkpoint_matches(
    checkpoint: dict[str, Any],
    chain_id: int,
    pool_address: str,
    block_start: int,
    block_end: int,
    chunk_size: int,
    pilot_start_utc: str | None = None,
    pilot_end_utc: str | None = None,
) -> bool:
    try:
        next_block = int(checkpoint.get("next_block", -1))
        return (
            int(checkpoint.get("chain_id", -1)) == chain_id
            and str(checkpoint.get("pool_address", "")).lower() == pool_address.lower()
            and int(checkpoint.get("block_start", -1)) == block_start
            and int(checkpoint.get("block_end", -1)) == block_end
            and int(checkpoint.get("chunk_size", -1)) == chunk_size
            and block_start <= next_block <= block_end + 1
            and (pilot_start_utc is None or checkpoint.get("pilot_start_utc") in {None, pilot_start_utc})
            and (pilot_end_utc is None or checkpoint.get("pilot_end_utc") in {None, pilot_end_utc})
        )
    except (TypeError, ValueError):
        return False


def _read_raw_records(path: Path) -> list[LogRecord]:
    if not path.exists():
        return []
    records: list[LogRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        records.append(log_record_from_rpc(raw, timestamp=int(raw["timestamp"])))
    return records


def _read_checkpoint_candidate(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _resolve_and_save(config: ResearchConfig, rpc: JsonRpcClient, root: Path) -> PoolMetadata:
    pool = resolve_pool(
        rpc,
        factory=config.factory,
        token_a=config.token0,
        token_b=config.token1,
        fee_tier=config.fee_tier,
        chain_id=config.chain_id,
    )
    write_json(_pool_path(root), asdict(pool))
    return pool


def command_resolve_pool(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(Path(args.config).resolve())
    pool = _resolve_and_save(config, _client(config), root)
    print(json.dumps(asdict(pool), ensure_ascii=False, sort_keys=True))
    return 0


def command_collect(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(Path(args.config).resolve())
    run_started = time.monotonic()
    chunk_size = config.collection_chunk_size if args.chunk_size is None else args.chunk_size
    request_interval_seconds = (
        config.request_interval_seconds
        if args.request_interval_seconds is None
        else args.request_interval_seconds
    )
    max_runtime_seconds = config.max_runtime_seconds if args.max_seconds is None else args.max_seconds
    if chunk_size <= 0 or request_interval_seconds < 0 or max_runtime_seconds <= 0:
        raise ValueError("chunk size must be positive and timing values must be valid")
    rpc = _client(config, args.rpc_timeout_seconds, args.rpc_max_retries)
    pool_path = _pool_path(root)
    pool = _load_pool(pool_path) if pool_path.exists() else _resolve_and_save(config, rpc, root)
    start_utc = args.start_utc or config.pilot_start_utc
    end_utc = args.end_utc or config.pilot_end_utc
    raw_dir = root / "data" / "raw" / f"chain={config.chain_id}" / f"pool={pool.pool_address}"
    normalized_dir = root / "data" / "normalized"
    raw_path = raw_dir / "logs.jsonl"
    parquet_path = normalized_dir / "events.parquet"
    checkpoint_path = _checkpoint_path(root)
    checkpoint: dict[str, Any] | None = None
    candidate = _read_checkpoint_candidate(checkpoint_path) if not args.fresh and raw_path.exists() else None
    if candidate is not None:
        candidate_manifest = _read_checkpoint_candidate(root / "results" / "dataset_manifest.json") or {}
        candidate_for_match = dict(candidate)
        manifest_metadata = candidate_manifest.get("metadata", {})
        if isinstance(manifest_metadata, dict):
            candidate_for_match.setdefault("pilot_start_utc", manifest_metadata.get("pilot_start_utc"))
            candidate_for_match.setdefault("pilot_end_utc", manifest_metadata.get("pilot_end_utc"))
        try:
            candidate_start = int(candidate.get("block_start", -1))
            candidate_end = int(candidate.get("block_end", -1))
        except (TypeError, ValueError):
            candidate_start, candidate_end = -1, -1
        if _checkpoint_matches(
            candidate_for_match,
            config.chain_id,
            pool.pool_address,
            candidate_start,
            candidate_end,
            chunk_size,
            start_utc,
            end_utc,
        ):
            checkpoint = candidate
    if checkpoint is not None:
        start_block = int(checkpoint["block_start"])
        end_block = int(checkpoint["block_end"])
    else:
        latest = rpc.latest_block()
        start_block = timestamp_to_block(
            rpc,
            _parse_utc(start_utc),
            latest,
            request_interval_seconds=request_interval_seconds,
        )
        end_block = timestamp_to_block(
            rpc,
            _parse_utc(end_utc),
            latest,
            request_interval_seconds=request_interval_seconds,
        )
    fresh_collection = checkpoint is None
    if fresh_collection:
        checkpoint = _new_checkpoint(
            config.chain_id,
            pool.pool_address,
            start_block,
            end_block,
            chunk_size,
            start_utc,
            end_utc,
        )
        write_jsonl(raw_path, [])
        parquet_path.unlink(missing_ok=True)
    checkpoint.setdefault("pilot_start_utc", start_utc)
    checkpoint.setdefault("pilot_end_utc", end_utc)
    records = _read_raw_records(raw_path)
    seen_keys = {record.stable_key for record in records}
    next_block = int(checkpoint["next_block"])
    completed = next_block > end_block
    budget_exhausted = time.monotonic() - run_started >= max_runtime_seconds
    if not completed and not budget_exhausted:
        for chunk in iter_log_chunks(
            rpc,
            pool.pool_address,
            next_block,
            end_block,
            chunk_size=chunk_size,
            request_interval_seconds=request_interval_seconds,
        ):
            chunk_end = min(next_block + chunk_size - 1, end_block)
            new_records = [record for record in chunk if record.stable_key not in seen_keys]
            append_jsonl(raw_path, [asdict(record) for record in new_records])
            seen_keys.update(record.stable_key for record in new_records)
            next_block = chunk_end + 1
            checkpoint.update(
                {
                    "next_block": next_block,
                    "chunks_completed": int(checkpoint.get("chunks_completed", 0)) + 1,
                    "logs_collected": len(seen_keys),
                    "last_chunk_start": chunk[0].block_number if chunk else chunk_end,
                    "last_chunk_end": chunk[-1].block_number if chunk else chunk_end,
                }
            )
            write_json(checkpoint_path, checkpoint)
            print(
                json.dumps(
                    {
                        "status": "progress",
                        "next_block": next_block,
                        "block_end": end_block,
                        "chunks_completed": checkpoint["chunks_completed"],
                        "logs_collected": len(seen_keys),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            if time.monotonic() - run_started >= max_runtime_seconds:
                break
        completed = next_block > end_block
    if not completed:
        checkpoint["status"] = "partial"
        write_json(checkpoint_path, checkpoint)
        records = _read_raw_records(raw_path)
        partial_manifest = build_manifest(
            status="partial",
            source="json-rpc",
            pool=pool,
            block_start=start_block,
            block_end=end_block,
            row_counts={"logs": len(records), "swaps": 0},
            checksums={str(raw_path.relative_to(root)): sha256_file(raw_path)},
            errors=["collection_in_progress: rerun collect to resume from the checkpoint"],
            metadata={
                "pilot_start_utc": start_utc,
                "pilot_end_utc": end_utc,
                "next_block": next_block,
                "request_interval_seconds": request_interval_seconds,
                "max_runtime_seconds": max_runtime_seconds,
            },
        )
        write_manifest(root / "results" / "dataset_manifest.json", partial_manifest)
        print(json.dumps(asdict(partial_manifest), ensure_ascii=False, sort_keys=True))
        return 2
    checkpoint["status"] = "complete"
    checkpoint["next_block"] = end_block + 1
    write_json(checkpoint_path, checkpoint)
    records = _read_raw_records(raw_path)
    rows = normalize_event_rows(records)
    status = "success"
    errors: list[str] = []
    if not records:
        status = "insufficient_data"
        errors.append("no pool logs were returned for the requested block range")
    elif not validate_log_order(records).valid:
        status = "invalid_dataset"
        errors.append("log stable keys are not strictly ordered and unique")
    else:
        write_parquet(parquet_path, rows)
    checksums = {str(raw_path.relative_to(root)): sha256_file(raw_path)}
    if parquet_path.exists():
        checksums[str(parquet_path.relative_to(root))] = sha256_file(parquet_path)
    manifest = build_manifest(
        status=status,
        source="json-rpc",
        pool=pool,
        block_start=start_block,
        block_end=end_block,
        row_counts={"logs": len(records), "swaps": sum(row.get("event_type") == "swap" for row in rows)},
        checksums=checksums,
        errors=errors,
        metadata={"pilot_start_utc": start_utc, "pilot_end_utc": end_utc},
    )
    manifest_path = root / "results" / "dataset_manifest.json"
    write_manifest(manifest_path, manifest)
    print(json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True))
    return 0 if status == "success" else 2


def _swap_events(path: Path) -> list[SwapEvent]:
    events: list[SwapEvent] = []
    for row in read_parquet_rows(path):
        if row.get("event_type") != "swap":
            continue
        events.append(
            SwapEvent(
                block_number=int(row["block_number"]),
                transaction_index=int(row["transaction_index"]),
                log_index=int(row["log_index"]),
                block_hash=str(row["block_hash"]),
                transaction_hash=str(row["transaction_hash"]),
                address=str(row["address"]),
                timestamp=int(row["timestamp"]),
                sender=str(row["sender"]),
                recipient=str(row["recipient"]),
                amount0=int(row["amount0"]),
                amount1=int(row["amount1"]),
                sqrt_price_x96=int(row["sqrt_price_x96"]),
                liquidity=int(row["liquidity"]),
                tick=int(row["tick"]),
            )
        )
    return events


def command_backtest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(Path(args.config).resolve())
    manifest_path = root / "results" / "dataset_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("dataset_manifest.json is missing; collect and validate data first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "success":
        summary = {"status": "insufficient_data", "reason": manifest.get("errors", [])}
        write_json(root / "results" / "backtest_summary.json", summary)
        write_report(root / "results" / "report.md", summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2
    pool = _load_pool(_pool_path(root))
    events = _swap_events(root / "data" / "normalized" / "events.parquet")
    if not events:
        summary = {"status": "insufficient_data", "reason": ["dataset contains no Swap events"]}
        write_json(root / "results" / "backtest_summary.json", summary)
        write_report(root / "results" / "report.md", summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2
    half_width = half_width_ticks_for_pct(config.half_width_pct, pool.tick_spacing)
    if config.strategy_name == "static_narrow":
        strategy = StaticRangeStrategy(half_width)
    else:
        strategy = ThresholdResetStrategy(
            half_width,
            config.trigger_ratio,
            config.cooldown_seconds,
            config.safety_margin_usd,
        )
    result = run_backtest(
        events,
        strategy,
        SimulationConfig(
            capital_usd=config.capital_usd,
            token0_decimals=pool.token0_decimals,
            token1_decimals=pool.token1_decimals,
            fee_tier=config.fee_tier,
            safety_margin_usd=config.safety_margin_usd,
        ),
    )
    first_price = price_token1_per_token0(events[0].sqrt_price_x96, pool.token0_decimals, pool.token1_decimals)
    final_price = price_token1_per_token0(events[-1].sqrt_price_x96, pool.token0_decimals, pool.token1_decimals)
    hodl = hodl_terminal_value(config.capital_usd / 2 / first_price, config.capital_usd / 2, final_price)
    from base_lp.metrics.performance import summarize_result

    summary: dict[str, Any] = {
        "status": "success",
        "research_status": "success"
        if events[-1].timestamp - events[0].timestamp >= 9 * 30 * 24 * 60 * 60
        else "insufficient_data",
        "counterfactual_mode": config.counterfactual_mode,
        "fee_precision": config.fee_precision,
        "events": len(events),
        "pool": asdict(pool),
        "hodl_terminal_value_usd": hodl,
        **summarize_result(result, hodl),
    }
    write_json(root / "results" / "backtest_summary.json", summary)
    write_report(root / "results" / "report.md", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    raw_path = next((root / "data" / "raw").rglob("logs.jsonl"), None)
    if raw_path is None:
        raise RuntimeError("raw logs.jsonl not found")
    records = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        records.append(log_record_from_rpc(raw, timestamp=int(raw["timestamp"])))
    report = validate_log_order(records)
    result = {"valid": report.valid, "duplicate_keys": report.duplicate_keys, "out_of_order_keys": report.out_of_order_keys}
    write_json(root / "results" / "validation_report.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if report.valid else 2


def command_sweep(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(Path(args.config).resolve())
    manifest_path = root / "results" / "dataset_manifest.json"
    parquet_path = root / "data" / "normalized" / "events.parquet"
    if not manifest_path.exists() or not parquet_path.exists():
        raise RuntimeError("dataset artifacts are missing; collect data first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "success":
        raise RuntimeError("parameter sweep requires a valid collected dataset")
    pool = _load_pool(_pool_path(root))
    events = _swap_events(parquet_path)
    simulation_config = SimulationConfig(
        capital_usd=config.capital_usd,
        token0_decimals=pool.token0_decimals,
        token1_decimals=pool.token1_decimals,
        fee_tier=config.fee_tier,
        safety_margin_usd=config.safety_margin_usd,
    )
    parameter_grid = [
        {"half_width_pct": half_width_pct, "trigger_ratio": trigger_ratio}
        for half_width_pct in (0.25, 0.5, 1.0)
        for trigger_ratio in (0.7, 0.85, 1.0)
    ]

    def strategy_factory(parameters: dict[str, Any]) -> ThresholdResetStrategy:
        return ThresholdResetStrategy(
            half_width_ticks=half_width_ticks_for_pct(float(parameters["half_width_pct"]), pool.tick_spacing),
            trigger_ratio=float(parameters["trigger_ratio"]),
            cooldown_seconds=config.cooldown_seconds,
            safety_margin_usd=config.safety_margin_usd,
        )

    rows = run_parameter_sweep(events, parameter_grid, strategy_factory, simulation_config)
    payload = {
        "status": "success",
        "research_status": assess_walk_forward(events)["status"],
        "events": len(events),
        "grid_size": len(rows),
        "rows": rows,
    }
    write_json(root / "results" / "experiment_grid.json", payload)
    csv_path = root / "results" / "experiment_grid.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "config_hash",
        "half_width_pct",
        "trigger_ratio",
        "strategy",
        "terminal_value_usd",
        "hodl_alpha_usd",
        "fees_usd",
        "costs_usd",
        "max_drawdown_usd",
        "rebalances",
        "time_in_range",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "config_hash": row["config_hash"],
                    "half_width_pct": row["config"]["half_width_pct"],
                    "trigger_ratio": row["config"]["trigger_ratio"],
                    **{key: row[key] for key in fieldnames[3:]},
                }
            )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Base Uniswap v3 LP research CLI")
    parser.add_argument("--root", default=str(ROOT), help="base-lp-research directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("resolve-pool", command_resolve_pool),
        ("collect", command_collect),
        ("validate-data", command_validate),
        ("backtest", command_backtest),
        ("sweep", command_sweep),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--config", default=str(DEFAULT_CONFIG))
        if name == "collect":
            sub.add_argument("--chunk-size", type=int)
            sub.add_argument("--start-utc")
            sub.add_argument("--end-utc")
            sub.add_argument("--request-interval-seconds", type=float)
            sub.add_argument("--rpc-timeout-seconds", type=float)
            sub.add_argument("--rpc-max-retries", type=int)
            sub.add_argument("--max-seconds", type=float)
            sub.add_argument("--fresh", action="store_true", help="discard the matching checkpoint and restart")
        sub.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
