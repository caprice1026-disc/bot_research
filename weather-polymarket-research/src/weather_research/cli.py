"""Command-line entry point for the research pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .backtest import ExecutionConfig, run_backtest_from_artifacts
from .collection import (
    collect_gefs_market_days,
    collect_gefs_target_day,
    collect_polymarket_target_day_prices,
    collect_research_data,
)
from .config import SOURCE_ENDPOINTS
from .reporting import validate_results, write_backtest_artifacts
from .source_audit import audit_sources

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weather-research",
        description="Point-in-time New York weather prediction-market research",
    )
    commands = parser.add_subparsers(dest="command")
    source_audit = commands.add_parser("source-audit", help="check configured public data sources")
    source_audit.add_argument("--output", type=Path, default=Path("results/source_audit.json"))
    collect = commands.add_parser("collect", help="collect raw market and weather data")
    collect.add_argument("--start-date", required=True)
    collect.add_argument("--end-date", required=True)
    collect.add_argument("--output-dir", type=Path, default=Path("data"))
    gefs_target = commands.add_parser("collect-gefs-target", help="collect GEFS target-day ensemble members")
    gefs_target.add_argument("--target-date", required=True)
    gefs_target.add_argument("--issue-time", required=True)
    gefs_target.add_argument("--output-dir", type=Path, default=Path("data"))
    gefs_target.add_argument("--max-members", type=int, default=21)
    gefs_days = commands.add_parser("collect-gefs-market-days", help="collect GEFS for every market target day")
    gefs_days.add_argument("--output-dir", type=Path, default=Path("data"))
    gefs_days.add_argument("--issue-cycle-hour", type=int, default=12)
    gefs_days.add_argument("--max-members", type=int, default=21)
    gefs_days.add_argument("--max-days", type=int)
    prices_target = commands.add_parser("collect-prices-target", help="collect CLOB prices for a target day")
    prices_target.add_argument("--target-date", required=True)
    prices_target.add_argument("--start-time", required=True)
    prices_target.add_argument("--end-time", required=True)
    prices_target.add_argument("--output-dir", type=Path, default=Path("data"))
    run = commands.add_parser("run-backtest", help="run the Level 1 and/or Level 2 backtest")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--results-dir", type=Path, default=Path("results"))
    run.add_argument("--level", choices=("1", "2", "both"), default="both")
    run.add_argument("--report", type=Path, default=Path("reports/backtest_report.md"))
    validate = commands.add_parser("validate-results", help="validate generated result artifacts")
    validate.add_argument("--results-dir", type=Path, default=Path("results"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "source-audit":
        result = audit_sources(SOURCE_ENDPOINTS)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "collect":
        manifest = collect_research_data(args.output_dir, args.start_date, args.end_date)
        print(json.dumps(manifest, ensure_ascii=False))
    elif args.command == "collect-gefs-target":
        manifest = collect_gefs_target_day(
            output_dir=args.output_dir,
            target_date=args.target_date,
            issue_time=args.issue_time,
            max_members=args.max_members,
        )
        print(json.dumps(manifest, ensure_ascii=False))
    elif args.command == "collect-gefs-market-days":
        manifest = collect_gefs_market_days(
            output_dir=args.output_dir,
            issue_cycle_hour=args.issue_cycle_hour,
            max_members=args.max_members,
            max_days=args.max_days,
        )
        print(json.dumps(manifest, ensure_ascii=False))
    elif args.command == "collect-prices-target":
        manifest = collect_polymarket_target_day_prices(
            output_dir=args.output_dir,
            target_date=args.target_date,
            start_time=args.start_time,
            end_time=args.end_time,
        )
        print(json.dumps(manifest, ensure_ascii=False))
    elif args.command == "run-backtest":
        levels = (1, 2) if args.level == "both" else (int(args.level),)
        outputs: dict[str, dict[str, object]] = {}
        for level in levels:
            result, build = run_backtest_from_artifacts(args.data_dir, ExecutionConfig(), level=level)
            suffix = "" if args.level != "both" or level == 1 else "_level2"
            report_path = args.report
            if suffix:
                report_path = args.report.with_name(f"{args.report.stem}{suffix}{args.report.suffix}")
            write_backtest_artifacts(
                result,
                args.results_dir,
                report_path,
                suffix=suffix,
                metadata={
                    "level": level,
                    "target_dates": build.target_dates,
                    "forecast_target_dates": build.forecast_target_dates,
                    "observation_target_dates": build.observation_target_dates,
                    "priced_markets": build.priced_markets,
                    "coverage_reasons": build.reasons,
                },
            )
            outputs[f"level_{level}"] = {
                "status": result.status.value,
                "reason": result.reason,
                "trade_count": len(result.trades),
            }
        print(json.dumps(outputs, ensure_ascii=False))
    elif args.command == "validate-results":
        result = validate_results(args.results_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("valid") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
