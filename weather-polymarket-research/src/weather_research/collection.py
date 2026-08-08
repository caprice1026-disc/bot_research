"""Read-only collection orchestration and dataset manifest generation."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .collectors.gefs import GefsCatalogClient
from .collectors.noaa import NceiClient
from .collectors.polymarket import CollectionError, PolymarketClient
from .config import NEW_YORK, SOURCE_ENDPOINTS, CityConfig
from .rules import InvalidMarketRule, parse_market_rule
from .schemas import OutcomeStatus
from .source_audit import audit_sources


def select_new_york_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for market in markets:
        text = f"{market.get('question', '')} {market.get('description', '')}".lower()
        is_new_york = any(term in text for term in ("new york", "nyc", "central park"))
        is_temperature = any(term in text for term in ("temperature", "weather", "high temp"))
        if is_new_york and is_temperature:
            selected.append(market)
    return selected


def filter_market_rules_by_target_date(
    rules: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    return [
        rule
        for rule in rules
        if start <= date.fromisoformat(str(rule["target_date"])) <= end
    ]


def observation_coverage_reason(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "NCEI returned no parseable observations"
    timestamps = sorted(str(observation["observation_time"]) for observation in observations)
    return f"NCEI observations cover {timestamps[0]} through {timestamps[-1]}"


def build_dataset_manifest(
    city: str,
    station_id: str,
    source_results: dict[str, dict[str, Any]],
    counts: dict[str, int],
    reasons: list[str],
) -> dict[str, Any]:
    if any(record.get("status") == "error" for record in source_results.values()):
        status = OutcomeStatus.COLLECTION_ERROR
    elif counts.get("market_rules", 0) == 0 or counts.get("observations", 0) == 0 or counts.get("forecast_members", 0) == 0:
        status = OutcomeStatus.INSUFFICIENT_DATA
    else:
        status = OutcomeStatus.SUCCESS
    return {
        "status": status.value,
        "city": city,
        "station_id": station_id,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_results": source_results,
        "counts": counts,
        "reasons": reasons,
    }


def _write_snapshot(raw_dir: Path, stem: str, payload: Any) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"{stem}-{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")


def collect_research_data(
    output_dir: Path,
    start_date: str,
    end_date: str,
    city: CityConfig = NEW_YORK,
) -> dict[str, Any]:
    data_dir = Path(output_dir)
    raw_dir = data_dir / "raw"
    normalized_dir = data_dir / "normalized"
    source_results = audit_sources(SOURCE_ENDPOINTS)
    reasons: list[str] = []
    markets: list[dict[str, Any]] = []
    selected_markets: list[dict[str, Any]] = []
    market_rules: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    forecast_members: list[dict[str, Any]] = []

    try:
        polymarket = PolymarketClient()
        markets = polymarket.search_markets("New York temperature")
        if not markets:
            markets = polymarket.search_markets("New York weather")
        selected_markets = select_new_york_markets(markets)
        _write_snapshot(raw_dir, "polymarket-markets", markets)
        _write_snapshot(raw_dir, "polymarket-new-york-markets", selected_markets)
        invalid_market_count = 0
        invalid_market_examples: list[str] = []
        for market in selected_markets:
            try:
                market_rules.append(parse_market_rule(market).model_dump(mode="json"))
            except InvalidMarketRule as exc:
                invalid_market_count += 1
                if len(invalid_market_examples) < 3:
                    invalid_market_examples.append(f"{market.get('id', '<unknown>')}: {exc}")
        if invalid_market_count:
            reasons.append(
                f"{invalid_market_count} selected markets failed rule parsing; examples: "
                + "; ".join(invalid_market_examples)
            )
        all_market_rules = market_rules
        market_rules = filter_market_rules_by_target_date(all_market_rules, start_date, end_date)
        outside_window_count = len(all_market_rules) - len(market_rules)
        if outside_window_count:
            reasons.append(f"{outside_window_count} valid market rules were outside the requested target-date window")
    except (CollectionError, httpx.HTTPError, RuntimeError) as exc:
        source_results["gamma"] = {"status": "error", "error": str(exc)}
        reasons.append(f"Polymarket collection failed: {exc}")

    try:
        ncei = NceiClient()
        parsed_observations = ncei.fetch_global_hourly(
            ncei_station_id=city.ncei_station_id,
            start_date=start_date,
            end_date=end_date,
            station_id=city.station_id,
        )
        observations = [row.model_dump(mode="json") for row in parsed_observations]
        _write_snapshot(raw_dir, "ncei-global-hourly", observations)
        reasons.append(observation_coverage_reason(observations))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        source_results["ncei"] = {"status": "error", "error": str(exc)}
        reasons.append(f"NCEI collection failed: {exc}")

    try:
        catalog = GefsCatalogClient().fetch_catalog(start_date=start_date, end_date=end_date)
        _write_snapshot(raw_dir, "gefs-catalog", asdict(catalog))
        if not catalog.issue_directories:
            reasons.append("GEFS AWS catalog returned no issue directories in the requested period")
        else:
            reasons.append(
                f"GEFS AWS catalog has {len(catalog.issue_directories)} issue dates, but GRIB2 members were not decoded"
            )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        source_results["gefs"] = {"status": "error", "error": str(exc)}
        reasons.append(f"GEFS collection failed: {exc}")

    _write_jsonl(normalized_dir / "market_rules.jsonl", market_rules)
    _write_jsonl(normalized_dir / "price_points.jsonl", [])
    _write_jsonl(normalized_dir / "observations.jsonl", observations)
    _write_jsonl(normalized_dir / "forecast_members.jsonl", forecast_members)
    manifest = build_dataset_manifest(
        city=city.slug,
        station_id=city.station_id,
        source_results=source_results,
        counts={
            "markets_raw": len(markets),
            "markets_selected": len(selected_markets),
            "market_rules": len(market_rules),
            "observations": len(observations),
            "forecast_members": len(forecast_members),
        },
        reasons=reasons,
    )
    results_dir = data_dir.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest
