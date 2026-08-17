from __future__ import annotations

import json
from pathlib import Path

from .data import load_inputs, quality_summary
from .descriptive import run_descriptive
from .features import build_hourly_features
from .regimes import run_regime_analysis
from .reporting import build_report, verify_results
from .walk_forward import run_walk_forward
from .artifacts import write_json_atomic


def run_pipeline(root: Path, config: dict[str, object], stage: str) -> dict[str, object]:
    if stage == "verify":
        return verify_results(root)
    frames = load_inputs(root, config)
    quality = quality_summary(frames, root)
    hourly_features = build_hourly_features(frames["1h"])
    results_dir = root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_path = results_dir / "analysis-summary.json"
    # An all-stage invocation is a clean rebuild.  Partial stages retain the
    # preceding stage summaries so they can be followed by --stage report.
    existing = {} if stage == "all" else (json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {})
    figures: list[str] = [] if stage == "all" else existing.get("figures", [])
    tables: list[str] = [] if stage == "all" else existing.get("tables", [])

    if stage in {"descriptive", "all"}:
        result = run_descriptive(frames, config, results_dir)
        existing["descriptive"] = result.summary
        figures = sorted(set(figures + result.figures))
        tables = sorted(set(tables + result.tables))
    if stage in {"regimes", "all"}:
        result = run_regime_analysis(hourly_features, config, results_dir)
        existing["regimes"] = result.summary
        figures = sorted(set(figures + result.figures))
        tables = sorted(set(tables + result.tables))
    if stage in {"walk-forward", "all"}:
        result = run_walk_forward(hourly_features, config, root, results_dir)
        existing["walk_forward"] = result.summary
        figures = sorted(set(figures + result.figures))
        tables = sorted(set(tables + result.tables))

    existing["quality"] = quality
    existing["figures"] = figures
    existing["tables"] = tables
    write_json_atomic(existing, summary_path)
    if stage in {"report", "all"}:
        required = ("descriptive", "regimes", "walk_forward")
        missing = [name for name in required if name not in existing]
        if missing:
            raise ValueError(f"missing analysis stages: {missing}")
        build_report(
            root,
            config,
            quality,
            existing["descriptive"],
            existing["regimes"],
            existing["walk_forward"],
            figures,
            tables,
        )
    return existing
