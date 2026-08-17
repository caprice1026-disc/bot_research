from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from btc_regime_eda.pipeline import run_pipeline
from btc_regime_eda.artifacts import write_json_atomic


def _canonical_summary_hash(path: Path) -> str:
    """Hash research values at their documented eight-decimal precision."""
    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, float):
            return round(value, 8)
        return value

    payload = normalize(json.loads(path.read_text(encoding="utf-8")))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BTCUSDT regime EDA research")
    parser.add_argument("--config", default="configs/research.json")
    parser.add_argument(
        "--stage",
        choices=("descriptive", "regimes", "walk-forward", "report", "all", "verify", "reproduce-check"),
        default="all",
    )
    args = parser.parse_args()
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    try:
        if args.stage == "reproduce-check":
            summary_path = ROOT / "results" / "analysis-summary.json"
            raw_before = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            before = _canonical_summary_hash(summary_path)
            run_pipeline(ROOT, config, "all")
            raw_after = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            after = _canonical_summary_hash(summary_path)
            result = {"status": "success" if before == after else "failed", "canonical_precision_decimals": 8, "before_sha256": before, "after_sha256": after, "raw_before_sha256": raw_before, "raw_after_sha256": raw_after}
            if before != after:
                raise ValueError(f"fixed-seed summary mismatch: {result}")
            # This file is intentionally not part of the reproducibility
            # comparison.  Write it only after the deterministic rebuild.
            write_json_atomic(result, ROOT / "results" / "reproducibility.json", ensure_ascii=True)
        else:
            result = run_pipeline(ROOT, config, args.stage)
    except Exception as error:
        print(f"research_error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result if args.stage in {"verify", "reproduce-check"} else {"stage": args.stage, "status": "success"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
