from __future__ import annotations

from btc5m.quality import validate_events


def test_quality_reports_duplicate_inversion_and_crossed_book() -> None:
    result = validate_events(
        [
            {
                "source": "binance",
                "sequence_id": "1",
                "local_receive_ts": "2026-09-07T00:00:01.000000Z",
                "bid": "0.60",
                "ask": "0.50",
            },
            {
                "source": "binance",
                "sequence_id": "1",
                "local_receive_ts": "2026-09-07T00:00:00.000000Z",
                "bid": "0.40",
                "ask": "0.50",
            },
        ]
    )

    assert result.total_rows == 2
    assert result.duplicate_sequence_count == 1
    assert result.timestamp_inversion_count == 1
    assert result.crossed_book_count == 1
    assert not result.valid


def test_quality_reports_missing_receive_timestamp() -> None:
    result = validate_events([{"source": "coinbase", "sequence_id": "x"}])

    assert result.missing_local_receive_count == 1
    assert not result.valid


def test_empty_quality_input_is_not_valid() -> None:
    result = validate_events([])

    assert result.total_rows == 0
    assert not result.valid


def test_quality_does_not_collide_sequences_across_channels() -> None:
    result = validate_events(
        [
            {
                "source": "binance",
                "symbol": "BTCUSDT",
                "event_type": "agg_trade",
                "sequence_id": "42",
                "local_receive_ts": "2026-09-07T00:00:00.000000Z",
            },
            {
                "source": "binance",
                "symbol": "BTCUSDT",
                "event_type": "book_ticker",
                "sequence_id": "42",
                "local_receive_ts": "2026-09-07T00:00:00.100000Z",
            },
        ]
    )

    assert result.duplicate_sequence_count == 0
