"""Fetch immutable public history for the two Chicago weather strategy studies."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "research/weather_config.json"
DATA = ROOT / "data/weather"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def request_url(base, params):
    return base + "?" + urllib.parse.urlencode(params, doseq=True)


def fetch(url, path, *, text=False, trim_trades=False):
    """Reuse completed responses only; preserve URL, checksum and retrieval time."""
    path = Path(path)
    if path.exists():
        stored = read_json(path)
        if stored["url"] != url:
            raise ValueError(f"Cached URL differs: {path}")
        return stored["body"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PolymarketStrategyResearch"})
    for attempt in range(4):
        try:
            with opener.open(req, timeout=60) as response:
                raw = response.read()
            body = raw.decode("utf-8") if text else json.loads(raw)
            if trim_trades:
                fields = {"condition_id", "token_id", "side", "outcome", "outcome_index", "price", "size", "timestamp", "transaction_hash"}
                body["data"] = [{k: v for k, v in row.items() if k in fields} for row in body["data"]]
            write_json(path, {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              "response_sha256": hashlib.sha256(raw).hexdigest(),
                              "body_identity_fields_removed": trim_trades, "body": body})
            return body
        except (urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def dates_between(start, end):
    value = date.fromisoformat(start)
    final = date.fromisoformat(end)
    while value <= final:
        yield value
        value += timedelta(days=1)


def event_slug(day):
    return f"highest-temperature-in-chicago-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"


def settlement_source(market):
    source = market.get("resolutionSource", "").lower()
    text = market.get("description", "")
    if "whole degrees Fahrenheit" not in text or "Chicago O'Hare Intl Airport Station" not in text:
        raise ValueError(f"Settlement station or rounding mismatch: {market.get('slug')}")
    if source == "https://www.weather.gov/wrh/timeseries?site=kord":
        return "NOAA_KORD_hourly" if "Show Hourly Data" in text else "NOAA_KORD"
    if source == "https://www.wunderground.com/history/daily/us/il/chicago/kord":
        return "Wunderground_KORD"
    raise ValueError(f"Unsupported settlement source: {source}")


def collect_day(day, cfg):
    ds = day.isoformat()
    event = fetch(request_url("https://gamma-api.polymarket.com/events", {"slug": event_slug(day)}), DATA / "events" / f"{ds}.json")
    forecast = fetch(request_url("https://single-runs-api.open-meteo.com/v1/forecast", {
        "latitude": cfg["latitude"], "longitude": cfg["longitude"], "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit", "timezone": cfg["timezone"],
        "models": ",".join(cfg["forecast_models"]), "run": f"{ds}T00:00", "forecast_days": 2,
    }), DATA / "forecasts" / f"{ds}.json")
    if forecast.get("utc_offset_seconds") != cfg["utc_offset_hours"] * 3600:
        raise ValueError("Configured constant UTC offset does not match forecast; do not cross DST")
    if len(event) != 1:
        return {"date": ds, "status": "missing_event", "markets": []}
    markets = []
    for market in event[0].get("markets", []):
        source = settlement_source(market)
        markets.append({"date": ds, "condition_id": market["conditionId"], "market_id": market["id"], "settlement_source": source})
    if not markets:
        raise ValueError(f"No markets in event: {ds}")
    return {"date": ds, "status": "ok", "markets": markets}


def collect_market(market):
    cid = market["condition_id"]
    cursor = None
    seen = set()
    count = 0
    for page in range(1000):
        params = {"condition": cid, "limit": 1000, "taker_only": "true"}
        if cursor is not None:
            params["cursor"] = cursor
        response = fetch(request_url("https://data-api.polymarket.com/v2/trades", params),
                         DATA / "trades" / cid / f"{page:04d}.json", trim_trades=True)
        rows = response["data"]
        if any(t["condition_id"] != cid for t in rows):
            raise ValueError("Trade query returned another market")
        count += len(rows)
        pagination = response.get("pagination", {})
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor")
        if not cursor or cursor in seen or not rows:
            raise ValueError("Pagination stopped before completion")
        seen.add(cursor)
    else:
        raise ValueError("Trade page safety cap reached")
    resolution = fetch(request_url("https://data-api.polymarket.com/v2/resolutions", {"condition": cid}),
                       DATA / "resolutions" / f"{cid}.json")
    resolved = [r for r in resolution.get("data", []) if r.get("condition_id") == cid and r.get("status") == "resolved"]
    return {**market, "trades": count, "pages": page + 1, "pagination_complete": True,
            "resolution_rows": len(resolved), "status": "ok" if len(resolved) == 1 else "missing_resolution"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    cfg = read_json(CONFIG)
    requested = list(dates_between(cfg["start_date"], cfg["end_date"]))
    # No fixed-offset shortcut outside this explicitly frozen summer window.
    if cfg["start_date"] < "2026-08-01" or cfg["end_date"] > "2026-09-30":
        raise ValueError("This study's timezone contract is August-September 2026 only")
    manifest = {"config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(), "requested_days": len(requested),
                "requested_start": cfg["start_date"], "requested_end": cfg["end_date"], "days": [], "markets": [], "errors": []}
    for label, jobs, func in [("days", requested, lambda d: collect_day(d, cfg))]:
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            futures = {pool.submit(func, job): job for job in jobs}
            for future in as_completed(futures):
                try:
                    row = future.result()
                    manifest[label].append(row)
                    print(f"day {row['date']} {row['status']} markets={len(row['markets'])}", flush=True)
                except Exception as exc:
                    manifest["errors"].append({"stage": label, "job": str(futures[future]), "error": str(exc)})
    all_markets = [m for day in manifest["days"] for m in day["markets"]]
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(collect_market, market): market for market in all_markets}
        for future in as_completed(futures):
            try:
                row = future.result()
                manifest["markets"].append(row)
                if len(manifest["markets"]) % 25 == 0:
                    print(f"market history {len(manifest['markets'])}/{len(all_markets)}", flush=True)
            except Exception as exc:
                manifest["errors"].append({"stage": "market", "job": futures[future], "error": str(exc)})
    try:
        # UTC range includes both edges of all requested local calendar days.
        observation_url = request_url("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", {
            "station": "ORD", "data": ["tmpf", "metar"], "sts": cfg["start_date"] + "T00:00:00Z",
            "ets": (requested[-1] + timedelta(days=2)).isoformat() + "T00:00:00Z",
            "tz": "UTC", "format": "onlycomma", "latlon": "no", "elev": "no", "missing": "M",
            "trace": "T", "direct": "no", "report_type": [3],
        })
        observations = fetch(observation_url, DATA / "observations_hourly.json", text=True)
        manifest["observation_csv_rows"] = max(0, len(observations.splitlines()) - 1)
    except Exception as exc:
        manifest["errors"].append({"stage": "observations", "error": str(exc)})
    manifest["days"].sort(key=lambda r: r["date"])
    manifest["markets"].sort(key=lambda r: (r["date"], r["condition_id"]))
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["complete"] = not manifest["errors"] and len(manifest["days"]) == len(requested) and all(d["status"] == "ok" for d in manifest["days"]) and all(m["status"] == "ok" for m in manifest["markets"])
    write_json(DATA / "manifest.json", manifest)
    print(json.dumps({"complete": manifest["complete"], "days": len(manifest["days"]), "markets": len(manifest["markets"]), "trades": sum(m["trades"] for m in manifest["markets"]), "errors": manifest["errors"]}, indent=2), flush=True)
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
