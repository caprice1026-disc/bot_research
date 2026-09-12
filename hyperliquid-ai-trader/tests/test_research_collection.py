from __future__ import annotations

import json

import pytest

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.collector import HyperliquidPublicCandleCollector
from hyperliquid_ai_trader.research.data import (
    CANDLE_INTERVAL_MS,
    ResearchDataError,
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


def _research_config_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "development-v1",
        "market": {
            "venue": "hyperliquid_mainnet_public",
            "symbol": "BTC",
            "interval": "1m",
        },
        "feature_set": "common_candles_v1",
        "api": {
            "allow_paid_api": False,
            "budget_usd": "0",
            "trader_model": "gemini-2.5-flash-lite",
            "reviewer_model": "gemini-3.6-flash",
        },
        "execution": {
            "model_delay_ms": 1000,
            "max_arrival_delay_ms": 60000,
            "max_hold_ms": 300000,
            "fee_rate": "0.00045",
            "spread_bps": "2",
            "slippage_bps": "1",
        },
        "simulation": {
            "initial_equity": "1000",
            "reference_notional": "250",
        },
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


def test_public_collector_rejects_ranges_beyond_provider_snapshot_limit() -> None:
    class FakeInfo:
        def candles_snapshot(self, *_: object) -> object:
            raise AssertionError("the provider must not be called for an oversized range")

    collector = HyperliquidPublicCandleCollector(info_client=FakeInfo(), coin="BTC")

    with pytest.raises(ResearchDataError, match="5,000"):
        collector.snapshot(
            start_ms=0,
            end_ms=5_000 * CANDLE_INTERVAL_MS + 1,
            received_at_ms=5_001 * CANDLE_INTERVAL_MS,
        )


def test_collect_cli_writes_confirmed_candles_and_source_manifest(tmp_path, monkeypatch) -> None:
    config = tmp_path / "research.json"
    config.write_text(json.dumps(_research_config_payload()), encoding="utf-8")
    output = tmp_path / "BTC-1m.jsonl"

    class FakeCollector:
        def snapshot(self, **kwargs):
            assert kwargs == {
                "start_ms": 0,
                "end_ms": 120_000,
                "received_at_ms": 180_000,
                "delivery_delay_ms": 0,
            }
            return normalize_hyperliquid_candles(
                raw_candles=[_raw_candle(0), _raw_candle(60_000)],
                coin="BTC",
                received_at_ms=180_000,
            )

    monkeypatch.setattr(
        cli,
        "create_hyperliquid_mainnet_public_collector",
        lambda *, coin: FakeCollector(),
        raising=False,
    )
    monkeypatch.setattr(cli, "_now_ms", lambda: 180_000, raising=False)

    assert cli.main(
        [
            "collect",
            "--config",
            str(config),
            "--start-ms",
            "0",
            "--end-ms",
            "120000",
            "--output",
            str(output),
        ]
    ) == 0
    assert len(read_normalized_candles_jsonl(output)) == 2
    manifest = json.loads(output.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == {
        "venue": "hyperliquid_mainnet_public",
        "symbol": "BTC",
        "interval": "1m",
        "requested_start_ms": 0,
        "requested_end_ms": 120_000,
        "received_at_ms": 180_000,
    }
    assert manifest["candle_count"] == 2
    assert len(manifest["content_sha256"]) == 64


def test_collect_cli_reports_insufficient_data_when_no_candle_is_confirmed(tmp_path, monkeypatch, capsys) -> None:
    config = tmp_path / "research.json"
    config.write_text(json.dumps(_research_config_payload()), encoding="utf-8")
    output = tmp_path / "BTC-1m.jsonl"

    class EmptyCollector:
        def snapshot(self, **_: object) -> list[object]:
            return []

    monkeypatch.setattr(
        cli,
        "create_hyperliquid_mainnet_public_collector",
        lambda *, coin: EmptyCollector(),
    )
    monkeypatch.setattr(cli, "_now_ms", lambda: 180_000)

    assert cli.main(
        [
            "collect",
            "--config",
            str(config),
            "--start-ms",
            "0",
            "--end-ms",
            "120000",
            "--output",
            str(output),
        ]
    ) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "insufficient_data"
    assert summary["reason"] == "no_confirmed_candles"
    assert not output.exists()
