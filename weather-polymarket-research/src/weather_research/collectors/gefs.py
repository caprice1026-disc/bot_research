"""NOMADS GEFS catalog discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from xml.etree import ElementTree

import httpx


def parse_gefs_catalog(html: str) -> list[str]:
    directories = sorted(set(re.findall(r"gefs\.\d{8}(?=[/\"])", html)))
    return directories


@dataclass(frozen=True)
class GefsCatalog:
    url: str
    fetched_at: datetime
    issue_directories: list[str]


class GefsCatalogClient:
    def __init__(
        self,
        base_url: str = "https://noaa-gefs-pds.s3.amazonaws.com/",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30.0)

    def fetch_catalog(self, start_date: str | None = None, end_date: str | None = None) -> GefsCatalog:
        query_prefixes = ["gefs."]
        if start_date or end_date:
            start = date.fromisoformat(start_date) if start_date else date.min
            end = date.fromisoformat(end_date) if end_date else date.max
            query_prefixes = [f"gefs.{year}" for year in range(start.year, end.year + 1)]
        prefixes: list[str] = []
        for query_prefix in query_prefixes:
            response = self.client.get(
                self.base_url,
                params={"list-type": "2", "prefix": query_prefix, "delimiter": "/"},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"GEFS catalog HTTP {response.status_code}: {response.text[:200]}")
            try:
                root = ElementTree.fromstring(response.text)
                prefixes.extend(element.text or "" for element in root.iter() if element.tag.endswith("Prefix"))
            except ElementTree.ParseError as exc:
                raise RuntimeError("GEFS S3 listing was not valid XML") from exc
        issue_directories = sorted(set(parse_gefs_catalog("\n".join(prefixes))))
        if start_date or end_date:
            issue_directories = [
                directory
                for directory in issue_directories
                if start <= date.fromisoformat(directory.removeprefix("gefs.")) <= end
            ]
        return GefsCatalog(
            url=self.base_url,
            fetched_at=datetime.now(timezone.utc),
            issue_directories=issue_directories,
        )
