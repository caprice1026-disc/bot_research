"""Small, offline-only command line surface for research configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Sequence

from .baseline import run_baseline
from .collector import create_hyperliquid_mainnet_public_collector
from .config import ResearchConfigError, load_research_config
from .data import (
    RESEARCH_DECISION_INTERVAL_MS,
    ResearchDataError,
    read_normalized_candles_jsonl,
    write_normalized_candles_jsonl,
)
from .points import (
    PointSelectionError,
    build_point_candidates,
    candidate_set_sha256,
    select_research_points,
    write_point_selection_jsonl,
)
from .simulator import SimulationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config", help="validate a public research JSON config")
    validate.add_argument("--config", type=Path, required=True)
    baseline = commands.add_parser("baseline", help="replay one fixed rule from normalized candles")
    baseline.add_argument("--config", type=Path, required=True)
    baseline.add_argument("--candles", type=Path, required=True)
    baseline.add_argument(
        "--baseline",
        choices=("always_abstain", "momentum", "mean_reversion"),
        required=True,
    )
    collect = commands.add_parser("collect", help="collect one public Hyperliquid 1m snapshot")
    collect.add_argument("--config", type=Path, required=True)
    collect.add_argument("--start-ms", type=int, required=True)
    collect.add_argument("--end-ms", type=int, required=True)
    collect.add_argument("--output", type=Path, required=True)
    points = commands.add_parser("points", help="select deterministic offline research points")
    points.add_argument("--config", type=Path, required=True)
    points.add_argument("--candles", type=Path, required=True)
    points.add_argument("--count", type=int, required=True)
    points.add_argument("--seed", type=int, default=42)
    points.add_argument("--output", type=Path, required=True)
    return parser


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _manifest_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".manifest.json")


def _write_artifact_manifest(*, output: Path, payload: dict[str, object]) -> Path:
    manifest_path = _manifest_path(output)
    manifest = {
        "schema_version": 1,
        **payload,
        "content_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    temporary = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_research_config(args.config)
        if args.command == "validate-config":
            print(json.dumps(config.public_summary(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "collect":
            if config.market_venue != "hyperliquid_mainnet_public":
                raise ResearchDataError("collect currently supports hyperliquid_mainnet_public only")
            received_at_ms = _now_ms()
            collector = create_hyperliquid_mainnet_public_collector(coin=config.symbol)
            candles = collector.snapshot(
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                received_at_ms=received_at_ms,
                delivery_delay_ms=0,
            )
            if not candles:
                print(
                    json.dumps(
                        {
                            "status": "insufficient_data",
                            "reason": "no_confirmed_candles",
                            "candle_count": 0,
                            "candles_path": str(args.output),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 3
            write_normalized_candles_jsonl(args.output, candles)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "source": {
                        "venue": config.market_venue,
                        "symbol": config.symbol,
                        "interval": "1m",
                        "requested_start_ms": args.start_ms,
                        "requested_end_ms": args.end_ms,
                        "received_at_ms": received_at_ms,
                    },
                    "candle_count": len(candles),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "candle_count": len(candles),
                        "candles_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "points":
            candles = read_normalized_candles_jsonl(args.candles)
            candidates = build_point_candidates(candles)
            try:
                selection = select_research_points(
                    candidates,
                    count=args.count,
                    seed=args.seed,
                )
            except PointSelectionError as error:
                if str(error).startswith("only "):
                    print(
                        json.dumps(
                            {
                                "status": "insufficient_data",
                                "candidate_count": len(candidates),
                                "requested_count": args.count,
                                "reason": "insufficient_point_candidates",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    return 3
                raise
            write_point_selection_jsonl(args.output, selection)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "experiment_id": config.experiment_id,
                    "feature_set": config.feature_set,
                    "seed": args.seed,
                    "decision_interval_ms": RESEARCH_DECISION_INTERVAL_MS,
                    "point_count": len(selection.points),
                    "candidate_count": len(candidates),
                    "candidate_set_sha256": candidate_set_sha256(candidates),
                    "candidate_stratum_counts": selection.candidate_stratum_counts,
                    "selected_stratum_counts": selection.stratum_counts,
                    "stratification": {
                        "return_5m": "positive_vs_non_positive",
                        "realized_vol_30m_median": selection.realized_vol_30m_median,
                        "volume_zscore": "nonnegative_vs_negative",
                    },
                    "source_candles_sha256": hashlib.sha256(args.candles.read_bytes()).hexdigest(),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "point_count": len(selection.points),
                        "points_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        result = run_baseline(
            candles=read_normalized_candles_jsonl(args.candles),
            baseline_name=args.baseline,
            execution_config=config.execution,
            initial_equity=config.initial_equity,
            reference_notional=config.reference_notional,
        )
    except (OSError, PointSelectionError, ResearchConfigError, ResearchDataError, SimulationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.public_summary(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
