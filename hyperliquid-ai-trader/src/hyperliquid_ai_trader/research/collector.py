"""Read-only Hyperliquid Mainnet public candle collection for research."""

from __future__ import annotations

from typing import Any, Protocol

from .data import NormalizedCandle, ResearchDataError, normalize_hyperliquid_candles


class CandleSnapshotClient(Protocol):
    def candles_snapshot(self, name: str, interval: str, startTime: int, endTime: int) -> Any: ...


class HyperliquidPublicCandleCollector:
    """Collect candles through ``Info`` only; it never receives a signer or key."""

    def __init__(self, *, info_client: CandleSnapshotClient, coin: str) -> None:
        self.info_client = info_client
        self.coin = coin
        session = getattr(info_client, "session", None)
        if session is not None:
            session.trust_env = False

    def snapshot(
        self,
        *,
        start_ms: int,
        end_ms: int,
        received_at_ms: int,
        delivery_delay_ms: int = 0,
    ) -> list[NormalizedCandle]:
        if start_ms < 0 or end_ms <= start_ms:
            raise ResearchDataError("snapshot range must be positive and ordered")
        raw_candles = self.info_client.candles_snapshot(self.coin, "1m", start_ms, end_ms)
        if not isinstance(raw_candles, list) or not all(isinstance(item, dict) for item in raw_candles):
            raise ResearchDataError("Hyperliquid returned an invalid candle snapshot")
        return normalize_hyperliquid_candles(
            raw_candles=raw_candles,
            coin=self.coin,
            received_at_ms=received_at_ms,
            delivery_delay_ms=delivery_delay_ms,
        )


def create_hyperliquid_mainnet_public_collector(*, coin: str, timeout_seconds: float = 15.0) -> HyperliquidPublicCandleCollector:
    """Create the SDK's unauthenticated Mainnet ``Info`` client for public data."""

    from hyperliquid.info import Info
    from hyperliquid.utils.constants import MAINNET_API_URL

    return HyperliquidPublicCandleCollector(
        info_client=Info(MAINNET_API_URL, skip_ws=True, timeout=timeout_seconds),
        coin=coin,
    )
