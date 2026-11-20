"""Restore only the public metadata required by the existing cohort test."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weather_followup import fetch_verified
from weather_data import dates_between, request_url


def one(day):
    ds = day.isoformat()
    slug = f"highest-temperature-in-chicago-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
    event = fetch_verified(request_url("https://gamma-api.polymarket.com/events", {"slug": slug}), ROOT / "data/weather/events" / (ds + ".json"))
    if len(event) != 1:
        raise ValueError("Missing prior test event")
    for market in event[0]["markets"]:
        cid = market["conditionId"]
        fetch_verified(request_url("https://data-api.polymarket.com/v2/resolutions", {"condition": cid}), ROOT / "data/weather/resolutions" / (cid + ".json"))
    print(ds, "public cohort test metadata restored", flush=True)


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, dates_between("2026-08-31", "2026-09-29")))
