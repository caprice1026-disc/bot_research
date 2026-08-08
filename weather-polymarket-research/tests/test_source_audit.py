import httpx

from weather_research.source_audit import audit_sources


def test_audit_sources_records_ok_and_error_per_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, text="ok")
        return httpx.Response(503, text="temporarily unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = audit_sources(
        {"gamma": "https://example.test/ok", "gefs": "https://example.test/error"},
        client=client,
    )

    assert result["gamma"]["status"] == "ok"
    assert result["gamma"]["http_status"] == 200
    assert result["gefs"]["status"] == "error"
    assert result["gefs"]["http_status"] == 503
    assert result["gamma"]["checked_at"]
