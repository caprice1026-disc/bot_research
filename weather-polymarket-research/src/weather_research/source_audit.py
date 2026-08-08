"""Small read-only availability audit for public data sources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


def audit_sources(
    endpoints: dict[str, str],
    client: httpx.Client | None = None,
) -> dict[str, dict[str, Any]]:
    own_client = client is None
    http = client or httpx.Client(timeout=30.0)
    result: dict[str, dict[str, Any]] = {}
    try:
        for name, url in endpoints.items():
            checked_at = datetime.now(timezone.utc).isoformat()
            record: dict[str, Any] = {"url": url, "checked_at": checked_at}
            try:
                response = http.get(url)
                record.update(
                    {
                        "status": "ok" if response.status_code < 400 else "error",
                        "http_status": response.status_code,
                        "content_bytes": len(response.content),
                    }
                )
                if response.status_code >= 400:
                    record["error"] = response.text[:200]
            except httpx.HTTPError as exc:
                record.update({"status": "error", "http_status": None, "error": str(exc)})
            result[name] = record
    finally:
        if own_client:
            http.close()
    return result
