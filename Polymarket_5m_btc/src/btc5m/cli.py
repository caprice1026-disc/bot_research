from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer

from btc5m.config import parse_sources
from btc5m.fixture import run_fixture
from btc5m.ingestion.pmxt import (
    coverage_manifest_to_dict,
    download_pmxt_hours,
    inspect_pmxt_coverage,
)
from btc5m.io import load_rows, write_json
from btc5m.quality import validate_events
from btc5m.research.lead_lag import event_study
from btc5m.storage import compact_jsonl_to_parquet

app = typer.Typer(
    name="btc5m",
    help="Public-data research tools for Polymarket BTC 5m markets.",
    no_args_is_help=True,
)


@app.command()
def collect(
    sources: str = typer.Option("all", help="Comma-separated public sources."),
    duration_seconds: int = typer.Option(
        60, min=1, help="Bounded collection duration."
    ),
    output_root: Path = typer.Option(Path("data"), help="Output directory."),
) -> None:
    """Collect public market events."""
    from btc5m.collectors.live import collect_public

    selected = parse_sources(sources)
    manifest = asyncio.run(collect_public(selected, output_root, duration_seconds))
    typer.echo(json.dumps({**manifest, "output": str(output_root)}))


@app.command()
def compact(
    input_path: Path = typer.Option(..., "--input", help="JSONL staging file."),
    output_path: Path = typer.Option(..., "--output", help="Parquet output file."),
) -> None:
    """Compact staging events into Parquet."""
    rows = compact_jsonl_to_parquet(input_path, output_path)
    typer.echo(json.dumps({"status": "ok", "rows": rows, "output": str(output_path)}))


@app.command()
def validate(
    input_path: Path = typer.Option(..., "--input", help="Event file."),
    output_path: Path = typer.Option(..., "--output", help="Quality JSON output."),
) -> None:
    """Validate event quality."""
    result = validate_events(load_rows(input_path))
    write_json(output_path, result.to_dict())
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False))


@app.command()
def fixture(
    output_root: Path = typer.Option(Path("data/fixture-run"), "--output-root"),
) -> None:
    """Run the deterministic offline fixture."""
    typer.echo(json.dumps(run_fixture(output_root), ensure_ascii=False, sort_keys=True))


@app.command("event-study")
@app.command("lead-lag")
def lead_lag(
    external_path: Path = typer.Option(..., "--external"),
    polymarket_path: Path = typer.Option(..., "--polymarket"),
    output_path: Path = typer.Option(..., "--output"),
) -> None:
    """Measure external-price and Polymarket response timing."""
    result = event_study(load_rows(external_path), load_rows(polymarket_path))
    write_json(output_path, result.to_dict())
    output_path.with_suffix(".md").write_text(result.to_markdown(), encoding="utf-8")
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False))


@app.command("ingest-pmxt")
def ingest_pmxt(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    download: bool = typer.Option(False, "--download"),
    output_root: Path = typer.Option(Path("data/pmxt"), "--output-root"),
) -> None:
    """Inspect or download bounded PMXT archive coverage."""
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if end_dt <= start_dt:
        raise typer.BadParameter("end must be after start")
    index_url = "https://archive.pmxt.dev/Polymarket/v2"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(index_url)
        response.raise_for_status()
        coverage = inspect_pmxt_coverage(start_dt, end_dt, response.text)
        result: dict[str, object] = {
            "coverage": coverage_manifest_to_dict(coverage),
            "index_url": index_url,
        }
        if download:
            result["download"] = download_pmxt_hours(
                coverage,
                output_root,
                client,
                allow_download=True,
            ).to_dict()
    write_json(output_root / "coverage.json", result)
    typer.echo(json.dumps(result, ensure_ascii=False, default=str))


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
