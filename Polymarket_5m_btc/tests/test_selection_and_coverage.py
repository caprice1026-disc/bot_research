from __future__ import annotations

from datetime import datetime, timezone

from btc5m.collectors.polymarket import market_identity_from_mapping
from btc5m.coverage import CoverageTracker, coverage_channel
from btc5m.research.selection import build_selection_report


def _row(
    source: str,
    event_type: str,
    received: str,
    *,
    symbol: str = "BTCUSDT",
    market_id: str | None = None,
) -> dict[str, object]:
    return {
        "source": source,
        "event_type": event_type,
        "local_receive_ts": received,
        "symbol": symbol,
        "market_id": market_id,
    }


def test_market_identity_rejects_15m_even_when_question_mentions_5m() -> None:
    market = {
        "slug": "btc-updown-15m-1700000000",
        "question": "BTC Up or Down 5m (rolling catalog label)",
        "condition_id": "condition-15m",
        "outcomes": {"yes": "up", "no": "down"},
    }

    assert market_identity_from_mapping(market) is None


def test_coverage_tracker_emits_channel_gap_interval() -> None:
    tracker = CoverageTracker(max_gap_seconds=1.0)
    tracker.observe(
        _row(
            "binance",
            "book_ticker",
            "2026-09-07T00:00:00.000000Z",
        )
    )
    tracker.observe(
        _row(
            "binance",
            "book_ticker",
            "2026-09-07T00:00:03.000000Z",
        )
    )

    assert len(tracker.gaps) == 1
    gap = tracker.gaps[0].to_dict()
    assert gap["source"] == "binance"
    assert gap["channel"] == "book_ticker"
    assert gap["duration_seconds"] == 3.0


def test_unquoted_price_change_cannot_satisfy_quote_continuity() -> None:
    assert (
        coverage_channel(
            {
                "source": "polymarket",
                "event_type": "price_change",
                "bid": None,
                "ask": None,
            }
        )
        == "price_change_unquoted"
    )


def test_selection_keeps_only_5m_and_requires_chainlink_history_and_horizon() -> None:
    five_minute = {
        "slug": "btc-updown-5m-1799366400",
        "condition_id": "condition-5m",
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "window_start_ts": 1_799_366_400_000_000,
        "window_end_ts": 1_799_366_700_000_000,
    }
    fifteen_minute = {
        "slug": "btc-updown-15m-1799366400",
        "condition_id": "condition-15m",
        "up_token_id": "up-15m",
        "down_token_id": "down-15m",
        "window_start_ts": 1_799_366_400_000_000,
        "window_end_ts": 1_799_367_300_000_000,
    }
    rows = [
        _row(
            "chainlink",
            "chainlink_twap_60",
            "2027-01-07T00:00:00.000000Z",
            symbol="btc/usd",
        ),
        _row(
            "chainlink",
            "chainlink_twap_60",
            "2027-01-07T00:05:00.000000Z",
            symbol="btc/usd",
        ),
        _row(
            "polymarket",
            "best_bid_ask",
            "2027-01-07T00:01:00.000000Z",
            symbol="up-token",
            market_id="condition-5m",
        ),
    ]

    report = build_selection_report(
        [five_minute, fifteen_minute],
        rows,
        lookback_ms=500,
        horizons_ms=(1000,),
        chainlink_history_seconds=60,
        max_gap_seconds=10_000,
    )

    assert report["selected_market_ids"] == ["condition-5m"]
    assert report["excluded_market_ids"] == ["condition-15m"]
    assert report["markets"][0]["analysis_start_ts"] == 1_799_366_340_000_000
    assert report["markets"][0]["analysis_end_ts"] == 1_799_366_701_000_000
    assert report["markets"][0]["eligible"] is False
    assert "missing_required_channel" in report["markets"][0]["reasons"]


def test_selection_does_not_treat_empty_rows_as_success() -> None:
    report = build_selection_report(
        [],
        [],
        lookback_ms=500,
        horizons_ms=(1000,),
        chainlink_history_seconds=60,
        max_gap_seconds=1.0,
    )

    assert report["status"] == "insufficient_data"
    assert report["selected_market_ids"] == []


def test_selection_requires_polymarket_quote_history_before_window_start() -> None:
    start_ts = 1_799_366_400_000_000
    end_ts = start_ts + 300_000_000
    start_iso = datetime.fromtimestamp(start_ts / 1_000_000, timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_ts / 1_000_000, timezone.utc).isoformat()
    market = {
        "slug": "btc-updown-5m-1799366400",
        "condition_id": "condition-5m",
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "window_start_ts": start_ts,
        "window_end_ts": end_ts,
    }
    report = build_selection_report(
        [market],
        [
            _row(
                "polymarket",
                "best_bid_ask",
                start_iso,
                symbol="up-token",
                market_id="condition-5m",
            ),
            _row(
                "polymarket",
                "best_bid_ask",
                end_iso,
                symbol="up-token",
                market_id="condition-5m",
            ),
        ],
        lookback_ms=500,
        horizons_ms=(1000,),
        chainlink_history_seconds=0,
        max_gap_seconds=10_000,
    )

    up_channel = next(
        channel
        for channel in report["markets"][0]["channels"]
        if channel["symbol"] == "up-token"
    )
    assert "insufficient_channel_window" in up_channel["reasons"]
