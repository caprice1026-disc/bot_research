"""Small, offline-only command line surface for research configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .baseline import run_baseline
from .config import ResearchConfigError, load_research_config
from .data import ResearchDataError, read_normalized_candles_jsonl
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_research_config(args.config)
        if args.command == "validate-config":
            print(json.dumps(config.public_summary(), ensure_ascii=False, sort_keys=True))
            return 0
        result = run_baseline(
            candles=read_normalized_candles_jsonl(args.candles),
            baseline_name=args.baseline,
            execution_config=config.execution,
            initial_equity=config.initial_equity,
            reference_notional=config.reference_notional,
        )
    except (OSError, ResearchConfigError, ResearchDataError, SimulationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.public_summary(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
