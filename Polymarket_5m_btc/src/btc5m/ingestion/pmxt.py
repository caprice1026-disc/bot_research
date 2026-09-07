from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

_ARCHIVE_BASE = "https://r2v2.pmxt.dev"
_FILE_RE = re.compile(
    r"polymarket_orderbook_(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2})\.parquet"
)


@dataclass(frozen=True, slots=True)
class CoverageManifest:
    requested_hours: tuple[datetime, ...]
    present_hours: tuple[datetime, ...]
    missing_hours: tuple[datetime, ...]
    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DownloadManifest:
    downloaded: tuple[str, ...]
    skipped: tuple[str, ...]
    missing: tuple[str, ...]
    errors: tuple[str, ...]
    sha256: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "downloaded": list(self.downloaded),
            "skipped": list(self.skipped),
            "missing": list(self.missing),
            "errors": list(self.errors),
            "sha256": {name: checksum for name, checksum in self.sha256},
        }


def _utc_hour(value: datetime) -> datetime:
    normalized = (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    )
    return normalized.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


def requested_utc_hours(start: datetime, end: datetime) -> list[datetime]:
    """Return every UTC hour touched by the half-open interval ``[start, end)``."""
    current = _utc_hour(start)
    normalized_end = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)
    normalized_end = normalized_end.astimezone(timezone.utc)
    hours: list[datetime] = []
    while current < normalized_end:
        hours.append(current)
        current += timedelta(hours=1)
    return hours


def pmxt_filename(hour: datetime) -> str:
    return f"polymarket_orderbook_{_utc_hour(hour):%Y-%m-%dT%H}.parquet"


def pmxt_url(hour: datetime) -> str:
    return f"{_ARCHIVE_BASE}/{pmxt_filename(hour)}"


def _hours_from_index(index_html: str) -> set[datetime]:
    found: set[datetime] = set()
    for match in _FILE_RE.finditer(index_html):
        parsed = datetime.strptime(match.group("stamp"), "%Y-%m-%dT%H")
        found.add(parsed.replace(tzinfo=timezone.utc))
    return found


def inspect_pmxt_coverage(
    start: datetime,
    end: datetime,
    index_html: str,
) -> CoverageManifest:
    requested = tuple(requested_utc_hours(start, end))
    available = _hours_from_index(index_html)
    present = tuple(hour for hour in requested if hour in available)
    missing = tuple(hour for hour in requested if hour not in available)
    return CoverageManifest(
        requested_hours=requested,
        present_hours=present,
        missing_hours=missing,
        urls=tuple(pmxt_url(hour) for hour in present),
    )


def _download_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_pmxt_hours(
    manifest: CoverageManifest,
    output_dir: Path,
    client: httpx.Client,
    *,
    allow_download: bool = False,
) -> DownloadManifest:
    """Download only listed archive hours, atomically and resumably.

    Existing files are treated as already validated inputs for this bounded
    importer.  Archive availability is reported separately from transport
    errors so missing hours cannot silently become zero-filled data.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    skipped: list[str] = []
    missing: list[str] = [pmxt_filename(hour) for hour in manifest.missing_hours]
    errors: list[str] = []
    checksums: list[tuple[str, str]] = []

    for url in manifest.urls:
        name = _download_name(url)
        target = output_dir / name
        if target.exists() and target.is_file() and target.stat().st_size >= 0:
            skipped.append(name)
            checksums.append((name, _sha256(target)))
            continue
        if not allow_download:
            errors.append(f"download disabled: {url}")
            continue
        temporary = target.with_name(target.name + ".part")
        try:
            with client.stream("GET", url) as response:
                if response.status_code == 404:
                    missing.append(name)
                    continue
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for block in response.iter_bytes():
                        handle.write(block)
            temporary.replace(target)
            downloaded.append(name)
            checksums.append((name, _sha256(target)))
        except Exception as exc:
            if temporary.exists():
                temporary.unlink()
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    return DownloadManifest(
        downloaded=tuple(downloaded),
        skipped=tuple(skipped),
        missing=tuple(dict.fromkeys(missing)),
        errors=tuple(errors),
        sha256=tuple(checksums),
    )


def coverage_manifest_to_dict(manifest: CoverageManifest) -> dict[str, object]:
    return {
        "requested_hours": [hour.isoformat() for hour in manifest.requested_hours],
        "present_hours": [hour.isoformat() for hour in manifest.present_hours],
        "missing_hours": [hour.isoformat() for hour in manifest.missing_hours],
        "urls": list(manifest.urls),
    }
