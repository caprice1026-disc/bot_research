import httpx

from weather_research.collectors.gefs import GefsCatalogClient, parse_gefs_catalog


def test_parse_gefs_catalog_finds_issue_directories() -> None:
    html = "<a href=\"gefs.20260101/\">gefs.20260101/</a><a href=\"gefs.20260102/\">gefs.20260102/</a>"

    assert parse_gefs_catalog(html) == ["gefs.20260101", "gefs.20260102"]


def test_gefs_catalog_client_records_reachable_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/"
        assert request.url.params["prefix"] == "gefs."
        return httpx.Response(
            200,
            text='<ListBucketResult><CommonPrefixes><Prefix>gefs.20260101/</Prefix></CommonPrefixes></ListBucketResult>',
        )

    client = GefsCatalogClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.fetch_catalog()

    assert result.issue_directories == ["gefs.20260101"]


def test_gefs_catalog_client_combines_all_requested_years() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        prefix = request.url.params["prefix"]
        if prefix == "gefs.2025":
            directory = "gefs.20251231"
        elif prefix == "gefs.2026":
            directory = "gefs.20260101"
        else:
            raise AssertionError(f"unexpected prefix: {prefix}")
        return httpx.Response(
            200,
            text=(
                "<ListBucketResult><CommonPrefixes>"
                f"<Prefix>{directory}/</Prefix>"
                "</CommonPrefixes></ListBucketResult>"
            ),
        )

    client = GefsCatalogClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.fetch_catalog(start_date="2025-12-01", end_date="2026-01-15")

    assert result.issue_directories == ["gefs.20251231", "gefs.20260101"]
