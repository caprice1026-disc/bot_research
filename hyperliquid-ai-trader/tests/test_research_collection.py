from __future__ import annotations

from hyperliquid_ai_trader.research.collector import HyperliquidPublicCandleCollector
from hyperliquid_ai_trader.research.data import (
    read_normalized_candles_jsonl,
    normalize_hyperliquid_candles,
    write_normalized_candles_jsonl,
)


def _raw_candle(start_ms: int, *, close: str = "100.5") -> dict[str, object]:
    return {
        "t": start_ms,
        "T": start_ms + 59_999,
        "o": "100.0",
        "h": "101.0",
        "l": "99.0",
        "c": close,
        "v": "10.0",
    }


def test_normalization_keeps_only_confirmed_hyperliquid_candles() -> None:
    candles = normalize_hyperliquid_candles(
        raw_candles=[_raw_candle(0), _raw_candle(60_000)],
        coin="BTC",
        received_at_ms=90_000,
        delivery_delay_ms=1_000,
    )

    assert len(candles) == 1
    assert candles[0].venue == "hyperliquid_mainnet_public"
    assert candles[0].close_exclusive_ms == 60_000
    assert candles[0].available_at_ms == 61_000
    assert candles[0].availability_kind == "historical_close_plus_delay"


def test_normalized_candle_jsonl_round_trip_is_time_safe(tmp_path) -> None:
    candles = normalize_hyperliquid_candles(
        raw_candles=[_raw_candle(0), _raw_candle(60_000)],
        coin="BTC",
        received_at_ms=120_000,
    )
    path = tmp_path / "candles.jsonl"

    write_normalized_candles_jsonl(path, candles)

    assert read_normalized_candles_jsonl(path) == candles


def test_public_collector_calls_only_candle_snapshot() -> None:
    class FakeInfo:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int, int]] = []

        def candles_snapshot(self, coin: str, interval: str, start_ms: int, end_ms: int):
            self.calls.append((coin, interval, start_ms, end_ms))
            return [_raw_candle(0)]

    info = FakeInfo()
    collector = HyperliquidPublicCandleCollector(info_client=info, coin="BTC")

    candles = collector.snapshot(start_ms=0, end_ms=60_000, received_at_ms=60_000)

    assert info.calls == [("BTC", "1m", 0, 60_000)]
    assert [candle.close for candle in candles] == [100.5]
