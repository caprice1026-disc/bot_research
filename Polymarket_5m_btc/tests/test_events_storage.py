from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import polars as pl

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent, parse_source_timestamp
from btc5m.storage import append_jsonl, compact_jsonl_to_parquet, partition_path


def test_source_timestamps_normalize_milliseconds_and_iso() -> None:
    assert parse_source_timestamp(1_700_000_000_123) == 1_700_000_000_123_000
    assert parse_source_timestamp("2023-11-14T22:13:20.123Z") == 1_700_000_000_123_000


def test_raw_event_serializes_decimal_and_raw_payload() -> None:
    received_at = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
    event = RawEvent.from_message(
        source="binance",
        symbol="BTCUSDT",
        event_type="agg_trade",
        payload={"p": "100.25", "m": False},
        received=ReceiveStamp(received_at, 123),
        source_event_ts=1_700_000_000_123,
        source_publish_ts=None,
        sequence_id="42",
        price=Decimal("100.25"),
        bid=None,
        ask=None,
        bid_size=None,
        ask_size=None,
    )

    row = event.to_row()

    assert row["price"] == "100.25"
    assert json.loads(row["raw_payload"]) == {"m": False, "p": "100.25"}
    assert row["source_event_ts"] == 1_700_000_000_123_000
    assert row["local_receive_ts"] == "2026-09-07T00:00:00.000000Z"


def test_raw_event_preserves_connection_id_separately_from_receive_id() -> None:
    event = RawEvent.from_message(
        source="binance",
        symbol="BTCUSDT",
        event_type="book_ticker",
        payload={"u": 7},
        received=ReceiveStamp(
            datetime(2026, 9, 7, tzinfo=timezone.utc),
            123,
        ),
        source_event_ts=1_700_000_000_123,
        source_publish_ts=None,
        sequence_id="7",
        price=None,
        bid=Decimal("100"),
        ask=Decimal("101"),
        bid_size=None,
        ask_size=None,
        connection_id="binance-connection-1",
    )

    row = event.to_row()

    assert row["connection_id"] == "binance-connection-1"
    assert row["receive_id"] != row["connection_id"]


def test_source_sequence_is_stable_while_receive_id_changes_on_replay() -> None:
    first = RawEvent.from_message(
        source="polymarket",
        symbol="up-token",
        event_type="book",
        payload={"hash": "book-hash"},
        received=ReceiveStamp(
            datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc), 100
        ),
        source_event_ts=1_700_000_000_000,
        source_publish_ts=None,
        sequence_id="source-sequence",
        price=None,
        bid=Decimal("0.59"),
        ask=Decimal("0.61"),
        bid_size=None,
        ask_size=None,
    )
    replay = RawEvent.from_message(
        source="polymarket",
        symbol="up-token",
        event_type="book",
        payload={"hash": "book-hash"},
        received=ReceiveStamp(
            datetime(2026, 9, 7, 0, 0, 0, 1, tzinfo=timezone.utc), 200
        ),
        source_event_ts=1_700_000_000_000,
        source_publish_ts=None,
        sequence_id="source-sequence",
        price=None,
        bid=Decimal("0.59"),
        ask=Decimal("0.61"),
        bid_size=None,
        ask_size=None,
    )

    assert first.sequence_id == replay.sequence_id
    assert first.receive_id != replay.receive_id
    assert first.to_row()["receive_id"] != replay.to_row()["receive_id"]


def test_partition_path_and_append_jsonl(tmp_path) -> None:
    received_at = datetime(2026, 9, 7, 3, 4, tzinfo=timezone.utc)
    path = partition_path(tmp_path, "coinbase", received_at)
    append_jsonl(path, {"source": "coinbase", "value": 1})

    assert (
        path == tmp_path / "coinbase" / "date=2026-09-07" / "hour=03" / "events.jsonl"
    )
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "source": "coinbase",
        "value": 1,
    }


def test_append_jsonl_rows_writes_a_batch(tmp_path) -> None:
    from btc5m.storage import append_jsonl_rows, read_jsonl

    path = tmp_path / "batch.jsonl"
    append_jsonl_rows(path, [{"source": "a", "value": 1}, {"source": "a", "value": 2}])

    assert read_jsonl(path) == [
        {"source": "a", "value": 1},
        {"source": "a", "value": 2},
    ]


def test_empty_compaction_creates_readable_parquet(tmp_path) -> None:
    input_path = tmp_path / "events.jsonl"
    input_path.write_text("", encoding="utf-8")
    output_path = tmp_path / "events.parquet"

    assert compact_jsonl_to_parquet(input_path, output_path) == 0
    assert output_path.exists()
    assert pl.read_parquet(output_path).height == 0
