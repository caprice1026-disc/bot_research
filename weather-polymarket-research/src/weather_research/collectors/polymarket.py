"""Read-only Polymarket Gamma and CLOB clients."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..schemas import PricePoint


class CollectionError(RuntimeError):
    """Raised when a public data source cannot be collected safely."""


class PolymarketClient:
    def __init__(
        self,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30.0)

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise CollectionError(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise CollectionError("response was not valid JSON") from exc

    def fetch_markets(self, page_size: int = 100, max_pages: int = 100) -> list[dict[str, Any]]:
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("page_size and max_pages must be positive")
        markets: list[dict[str, Any]] = []
        for page in range(max_pages):
            response = self.client.get(
                f"{self.gamma_base_url}/markets",
                params={"limit": page_size, "offset": page * page_size},
            )
            payload = self._json(response)
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise CollectionError("Gamma markets response must be a list of objects")
            markets.extend(payload)
            if len(payload) < page_size:
                return markets
        raise CollectionError("Gamma markets pagination exceeded max_pages")

    def search_markets(self, query: str, limit_per_type: int = 100) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        response = self.client.get(
            f"{self.gamma_base_url}/public-search",
            params={"q": query, "limit_per_type": limit_per_type, "page": 1, "keep_closed_markets": 1},
        )
        payload = self._json(response)
        if not isinstance(payload, dict):
            raise CollectionError("public search response must be an object")
        markets: list[dict[str, Any]] = []
        direct_markets = payload.get("markets", [])
        if isinstance(direct_markets, list):
            markets.extend(item for item in direct_markets if isinstance(item, dict))
        events = payload.get("events", [])
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict) and isinstance(event.get("markets"), list):
                    markets.extend(item for item in event["markets"] if isinstance(item, dict))
        deduplicated: dict[str, dict[str, Any]] = {}
        for market in markets:
            market_id = str(market.get("id", ""))
            if market_id:
                deduplicated[market_id] = market
        return [deduplicated[market_id] for market_id in sorted(deduplicated)]

    def fetch_price_history(
        self,
        market_id: str,
        token_id: str,
        start_ts: int,
        end_ts: int,
        fidelity: int = 60,
    ) -> list[PricePoint]:
        response = self.client.post(
            f"{self.clob_base_url}/batch-prices-history",
            json={"markets": [token_id], "start_ts": start_ts, "end_ts": end_ts, "fidelity": fidelity},
        )
        payload = self._json(response)
        history = payload.get("history", {}).get(token_id, []) if isinstance(payload, dict) else []
        if not isinstance(history, list):
            raise CollectionError("CLOB history for token is not a list")
        points: list[PricePoint] = []
        for item in history:
            try:
                timestamp = datetime.fromtimestamp(float(item["t"]), tz=timezone.utc)
                price = float(item["p"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise CollectionError("CLOB history contains an invalid point") from exc
            points.append(PricePoint(market_id=market_id, token_id=token_id, timestamp=timestamp, price=price))
        return points
