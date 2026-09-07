from __future__ import annotations

from btc5m.collectors.live import (
    binance_stream_url,
    coinbase_subscribe_payloads,
    hyperliquid_subscribe_payloads,
)


def test_binance_combined_stream_contains_trade_and_book_ticker() -> None:
    url = binance_stream_url("BTCUSDT")

    assert url == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@aggTrade/btcusdt@bookTicker"
    )


def test_coinbase_subscriptions_are_public_btc_messages() -> None:
    messages = coinbase_subscribe_payloads(["BTC-USD"])

    assert messages == [
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker"},
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "market_trades"},
    ]


def test_hyperliquid_subscriptions_cover_trades_and_bbo() -> None:
    messages = hyperliquid_subscribe_payloads("BTC")

    assert messages == [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}},
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": "BTC"}},
    ]
