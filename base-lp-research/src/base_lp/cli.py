from __future__ import annotations

import argparse
import csv
import hashlib
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
from base_lp.data.dune import DuneClient, DuneError, build_swap_logs_sql, dune_row_to_log_record
from base_lp.data.events import SWAP_TOPIC
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


def _dune_checkpoint_path(root: Path) -> Path:
    return root / "results" / "dune_collection_checkpoint.json"


def _dotenv_value(root: Path, name: str) -> str | None:
    for candidate in (root / ".env", root.parent / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"").strip("'")
    return None


def _dune_client(config: ResearchConfig, root: Path) -> DuneClient:
    api_key = os.environ.get(config.dune_api_env) or _dotenv_value(root, config.dune_api_env)
    if not api_key:
        raise RuntimeError(f"missing required Dune API key: {config.dune_api_env}")
    return DuneClient(api_key)


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
    if config.event_filter not in {"all", "swap"}:
        raise ValueError(f"unsupported collection event filter: {config.event_filter}")
    if (args.start_block is None) != (args.end_block is None):
        raise ValueError("start-block and end-block must be provided together")
    if args.start_block is not None and (args.start_block < 0 or args.end_block < args.start_block):
        raise ValueError("block boundaries must be non-negative and ordered")
    topics = [SWAP_TOPIC] if config.event_filter == "swap" else None
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
        expected_start_block = args.start_block if args.start_block is not None else candidate_start
        expected_end_block = args.end_block if args.end_block is not None else candidate_end
        if _checkpoint_matches(
            candidate_for_match,
            config.chain_id,
            pool.pool_address,
            expected_start_block,
            expected_end_block,
            chunk_size,
            start_utc,
            end_utc,
        ):
            checkpoint = candidate
    if checkpoint is not None:
        start_block = int(checkpoint["block_start"])
        end_block = int(checkpoint["block_end"])
    elif args.start_block is not None:
        start_block = args.start_block
        end_block = args.end_block
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
            topics=topics,
        ):
            chunk_end = min(next_block + chunk_size - 1, end_block)
            append_jsonl(raw_path, [asdict(record) for record in chunk])
            next_block = chunk_end + 1
            checkpoint.update(
                {
                    "next_block": next_block,
                    "chunks_completed": int(checkpoint.get("chunks_completed", 0)) + 1,
                    "logs_collected": int(checkpoint.get("logs_collected", 0)) + len(chunk),
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
                        "logs_collected": checkpoint["logs_collected"],
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
        partial_manifest = build_manifest(
            status="partial",
            source="json-rpc",
            pool=pool,
            block_start=start_block,
            block_end=end_block,
            row_counts={
                "logs": int(checkpoint.get("logs_collected", 0)),
                "swaps": int(checkpoint.get("logs_collected", 0)) if topics else 0,
            },
            checksums={},
            errors=["collection_in_progress: rerun collect to resume from the checkpoint"],
            metadata={
                "pilot_start_utc": start_utc,
                "pilot_end_utc": end_utc,
                "next_block": next_block,
                "request_interval_seconds": request_interval_seconds,
                "max_runtime_seconds": max_runtime_seconds,
                "event_filter": config.event_filter,
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
        metadata={
            "pilot_start_utc": start_utc,
            "pilot_end_utc": end_utc,
            "event_filter": config.event_filter,
        },
    )
    manifest_path = root / "results" / "dataset_manifest.json"
    write_manifest(manifest_path, manifest)
    print(json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True))
    return 0 if status == "success" else 2


def command_collect_dune(args: argparse.Namespace) -> int:
    """Collect one bounded Swap-only dataset through one resumable Dune SQL execution."""

    root = Path(args.root).resolve()
    config = load_config(Path(args.config).resolve())
    pool_path = _pool_path(root)
    if not pool_path.exists():
        raise RuntimeError("pool_metadata.json is missing; resolve the pool once before Dune collection")
    pool = _load_pool(pool_path)
    start_utc = args.start_utc or config.pilot_start_utc
    end_utc = args.end_utc or config.pilot_end_utc
    page_size = args.page_size
    poll_interval_seconds = (
        config.dune_poll_interval_seconds
        if args.poll_interval_seconds is None
        else args.poll_interval_seconds
    )
    max_wait_seconds = args.max_wait_seconds
    if page_size <= 0 or poll_interval_seconds < 0 or max_wait_seconds <= 0:
        raise ValueError("Dune paging and wait timing values must be valid")
    sql = build_swap_logs_sql(pool.pool_address, start_utc, end_utc)
    sql_sha256 = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    raw_path = (
        root
        / "data"
        / "raw"
        / "source=dune"
        / f"chain={config.chain_id}"
        / f"pool={pool.pool_address}"
        / "logs.jsonl"
    )
    parquet_path = root / "data" / "normalized" / "events.parquet"
    manifest_path = root / "results" / "dataset_manifest.json"
    checkpoint_path = _dune_checkpoint_path(root)
    checkpoint = _read_checkpoint_candidate(checkpoint_path) if not getattr(args, "fresh", False) else None
    matching_checkpoint = (
        checkpoint
        if checkpoint
        and checkpoint.get("pool_address") == pool.pool_address
        and checkpoint.get("start_utc") == start_utc
        and checkpoint.get("end_utc") == end_utc
        and checkpoint.get("sql_sha256") == sql_sha256
        else None
    )
    if checkpoint is not None and matching_checkpoint is None:
        append_jsonl(root / "results" / "dune_collection_history.jsonl", [checkpoint])
    if (
        matching_checkpoint
        and matching_checkpoint.get("status") == "complete"
        and raw_path.exists()
        and manifest_path.exists()
    ):
        existing = _read_checkpoint_candidate(manifest_path)
        if existing and existing.get("status") == "success" and existing.get("source") == "dune-sql":
            print(json.dumps(existing, ensure_ascii=False, sort_keys=True))
            return 0

    dune = _dune_client(config, root)
    execution_id = matching_checkpoint.get("execution_id") if matching_checkpoint else None
    if not isinstance(execution_id, str) or not execution_id:
        execution_id = dune.execute_sql(sql)
        checkpoint = {
            "status": "submitted",
            "execution_id": execution_id,
            "pool_address": pool.pool_address,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "sql_sha256": sql_sha256,
        }
        write_json(checkpoint_path, checkpoint)
    result_columns = [
        "block_time",
        "block_number",
        "block_hash",
        "topic1",
        "topic2",
        "data",
        "tx_hash",
        "log_index",
        "tx_index",
    ]
    try:
        dune.wait_for_completion(
            execution_id,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=max_wait_seconds,
        )
        records = [
            dune_row_to_log_record(
                {"contract_address": pool.pool_address, "topic0": SWAP_TOPIC, **row},
            )
            for row in dune.iter_result_rows(execution_id, page_size=page_size, columns=result_columns)
        ]
    except DuneError as exc:
        error_message = f"dune_collection_error: {exc}"
        error_manifest = build_manifest(
            status="collection_error",
            source="dune-sql",
            pool=pool,
            block_start=pool.creation_block,
            block_end=pool.creation_block,
            row_counts={"logs": 0, "swaps": 0},
            checksums={},
            errors=[error_message],
            metadata={
                "dune_execution_id": execution_id,
                "sql_sha256": sql_sha256,
                "pilot_start_utc": start_utc,
                "pilot_end_utc": end_utc,
                "page_size": page_size,
                "poll_interval_seconds": poll_interval_seconds,
                "event_filter": "Swap",
                "result_columns": result_columns,
            },
        )
        write_manifest(manifest_path, error_manifest)
        write_json(
            checkpoint_path,
            {
                "status": "result_error",
                "execution_id": execution_id,
                "pool_address": pool.pool_address,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "sql_sha256": sql_sha256,
                "error": error_message,
            },
        )
        print(json.dumps(asdict(error_manifest), ensure_ascii=False, sort_keys=True))
        return 2
    records.sort(key=lambda record: record.stable_key)
    write_jsonl(raw_path, [asdict(record) for record in records])
    rows = normalize_event_rows(records)
    errors: list[str] = []
    status = "success"
    if not records:
        status = "insufficient_data"
        errors.append("Dune returned no Swap logs for the requested UTC window")
    elif not validate_log_order(records).valid:
        status = "invalid_dataset"
        errors.append("Dune log stable keys are not strictly ordered and unique")
    else:
        write_parquet(parquet_path, rows)
    checksums = {str(raw_path.relative_to(root)): sha256_file(raw_path)}
    if parquet_path.exists() and status == "success":
        checksums[str(parquet_path.relative_to(root))] = sha256_file(parquet_path)
    block_start = min((record.block_number for record in records), default=pool.creation_block)
    block_end = max((record.block_number for record in records), default=pool.creation_block)
    manifest = build_manifest(
        status=status,
        source="dune-sql",
        pool=pool,
        block_start=block_start,
        block_end=block_end,
        row_counts={"logs": len(records), "swaps": len(rows)},
        checksums=checksums,
        errors=errors,
        metadata={
            "dune_execution_id": execution_id,
            "sql_sha256": sql_sha256,
            "pilot_start_utc": start_utc,
            "pilot_end_utc": end_utc,
            "page_size": page_size,
            "poll_interval_seconds": poll_interval_seconds,
            "event_filter": "Swap",
            "result_columns": result_columns,
        },
    )
    write_manifest(manifest_path, manifest)
    write_json(
        checkpoint_path,
        {
            "status": "complete" if status == "success" else status,
            "execution_id": execution_id,
            "pool_address": pool.pool_address,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "sql_sha256": sql_sha256,
        },
    )
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
    partial_dataset = manifest.get("status") != "success"
    if partial_dataset and not args.allow_partial:
        summary = {"status": "insufficient_data", "reason": manifest.get("errors", [])}
        write_json(root / "results" / "backtest_summary.json", summary)
        write_report(root / "results" / "report.md", summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2
    pool = _load_pool(_pool_path(root))
    parquet_path = root / "data" / "normalized" / "events.parquet"
    if partial_dataset and not parquet_path.exists():
        raw_path = next((root / "data" / "raw").rglob("logs.jsonl"), None)
        if raw_path is None:
            summary = {"status": "insufficient_data", "reason": ["partial dataset raw logs are missing"]}
            write_json(root / "results" / "backtest_summary.json", summary)
            write_report(root / "results" / "report.md", summary)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 2
        records = _read_raw_records(raw_path)
        if not validate_log_order(records).valid:
            summary = {"status": "insufficient_data", "reason": ["partial dataset log order is invalid"]}
            write_json(root / "results" / "backtest_summary.json", summary)
            write_report(root / "results" / "report.md", summary)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 2
        write_parquet(parquet_path, normalize_event_rows(records))
    events = _swap_events(parquet_path)
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
        "collection_status": manifest.get("status"),
        "analysis_scope": "partial_collected_range" if partial_dataset else "complete_dataset",
        "warnings": ["backtest uses an incomplete collection; do not generalize to the full seven-day window"]
        if partial_dataset
        else [],
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
    manifest = _read_checkpoint_candidate(root / "results" / "dataset_manifest.json") or {}
    checksum_paths = manifest.get("checksums", {})
    raw_path = None
    if isinstance(checksum_paths, dict):
        raw_path = next(
            (root / relative for relative in checksum_paths if str(relative).endswith("logs.jsonl")),
            None,
        )
    if raw_path is None or not raw_path.exists():
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
        ("collect-dune", command_collect_dune),
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
            sub.add_argument("--start-block", type=int)
            sub.add_argument("--end-block", type=int)
            sub.add_argument("--request-interval-seconds", type=float)
            sub.add_argument("--rpc-timeout-seconds", type=float)
            sub.add_argument("--rpc-max-retries", type=int)
            sub.add_argument("--max-seconds", type=float)
            sub.add_argument("--fresh", action="store_true", help="discard the matching checkpoint and restart")
        if name == "backtest":
            sub.add_argument("--allow-partial", action="store_true", help="run an exploratory backtest on partial collection data")
        if name == "collect-dune":
            sub.add_argument("--start-utc")
            sub.add_argument("--end-utc")
            sub.add_argument("--page-size", type=int, default=10_000)
            sub.add_argument("--poll-interval-seconds", type=float)
            sub.add_argument("--max-wait-seconds", type=float, default=900.0)
            sub.add_argument("--fresh", action="store_true", help="execute a new Dune SQL request")
        sub.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
