from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent
from btc5m.io import write_json
from btc5m.quality import validate_events
from btc5m.research.lead_lag import event_study
from btc5m.storage import append_jsonl, compact_jsonl_to_parquet, partition_path


def _event(
    *,
    source: str,
    received: datetime,
    sequence: str,
    price: str,
) -> RawEvent:
    return RawEvent.from_message(
        source=source,
        symbol="BTCUSDT" if source == "binance" else "fixture-up",
        event_type="fixture_price",
        payload={"fixture": True, "price": price},
        received=ReceiveStamp(received, int(received.timestamp() * 1_000_000_000)),
        source_event_ts=received,
        source_publish_ts=received,
        sequence_id=sequence,
        price=Decimal(price),
        bid=None,
        ask=None,
        bid_size=None,
        ask_size=None,
    )


def run_fixture(output_root: Path) -> dict[str, object]:
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    external = [
        _event(source="binance", received=start, sequence="b0", price="100"),
        _event(
            source="binance",
            received=start + timedelta(milliseconds=500),
            sequence="b1",
            price="101",
        ),
    ]
    polymarket = [
        _event(source="polymarket", received=start, sequence="p0", price="0.50"),
        _event(
            source="polymarket",
            received=start + timedelta(milliseconds=700),
            sequence="p1",
            price="0.60",
        ),
    ]
    rows_by_source = {"binance": external, "polymarket": polymarket}
    normalized = output_root / "normalized"
    staging = output_root / "raw_staging"
    quality_reports: dict[str, object] = {}
    normalized_paths: dict[str, Path] = {}
    for source, events in rows_by_source.items():
        staging_path = partition_path(staging, source, start)
        for event in events:
            append_jsonl(staging_path, event.to_row())
        normalized_path = normalized / f"{source}.parquet"
        compact_jsonl_to_parquet(staging_path, normalized_path)
        normalized_paths[source] = normalized_path
        quality_reports[source] = validate_events(
            [event.to_row() for event in events]
        ).to_dict()

    result = event_study(
        [event.to_row() for event in external],
        [event.to_row() for event in polymarket],
        shock_return=0.005,
        horizons_ms=(100, 200),
    )
    write_json(output_root / "reports" / "fixture_quality.json", quality_reports)
    write_json(output_root / "reports" / "fixture_lead_lag.json", result.to_dict())
    markdown_path = output_root / "reports" / "fixture_lead_lag.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(result.to_markdown(), encoding="utf-8")
    return {
        "status": "completed",
        "result_state": result.status,
        "normalized": {source: str(path) for source, path in normalized_paths.items()},
        "reports": {
            "quality": str(output_root / "reports" / "fixture_quality.json"),
            "lead_lag": str(output_root / "reports" / "fixture_lead_lag.json"),
            "lead_lag_markdown": str(markdown_path),
        },
    }
