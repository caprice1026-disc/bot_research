import json

import httpx

from weather_research.collectors.polymarket import PolymarketClient


def test_polymarket_client_pages_gamma_markets_and_reads_clob_history() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/markets":
            offset = int(request.url.params["offset"])
            if offset == 0:
                return httpx.Response(200, json=[{"id": "m1"}])
            return httpx.Response(200, json=[])
        if request.url.path == "/batch-prices-history":
            body = json.loads(request.content)
            assert body["markets"] == ["yes-token"]
            return httpx.Response(
                200,
                json={"history": {"yes-token": [{"t": 1767351600, "p": 0.40}]}},
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = PolymarketClient(
        gamma_base_url="https://gamma.test",
        clob_base_url="https://clob.test",
        client=client,
    )

    markets = api.fetch_markets(page_size=1)
    history = api.fetch_price_history(
        market_id="m1",
        token_id="yes-token",
        start_ts=1767350000,
        end_ts=1767352000,
    )

    assert markets == [{"id": "m1"}]
    assert history[0].market_id == "m1"
    assert history[0].token_id == "yes-token"
    assert history[0].price == 0.40
    assert calls == ["GET /markets", "GET /markets", "POST /batch-prices-history"]


def test_polymarket_client_searches_closed_markets_by_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public-search"
        assert request.url.params["q"] == "New York temperature"
        assert request.url.params["keep_closed_markets"] == "1"
        return httpx.Response(
            200,
            json={"events": [{"markets": [{"id": "m1"}]}], "markets": [{"id": "m2"}]},
        )

    api = PolymarketClient(
        gamma_base_url="https://gamma.test",
        clob_base_url="https://clob.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert api.search_markets("New York temperature") == [{"id": "m1"}, {"id": "m2"}]


def test_polymarket_client_preserves_optional_historical_bid_and_ask_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"history": {"yes-token": [{"t": 1767351600, "p": 0.40, "b": 0.39, "a": 0.41}]}},
        )

    api = PolymarketClient(
        gamma_base_url="https://gamma.test",
        clob_base_url="https://clob.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    point = api.fetch_price_history("m1", "yes-token", 1, 2)[0]

    assert point.best_bid == 0.39
    assert point.best_ask == 0.41
