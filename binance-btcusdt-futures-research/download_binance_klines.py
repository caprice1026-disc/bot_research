"""Download and validate one rolling year of Binance USD-M BTCUSDT klines."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time
from typing import Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import zipfile


BASE_URL = "https://data.binance.vision/data/futures/um"
SYMBOL = "BTCUSDT"
INTERVALS = ("1d", "1h", "15m")
INTERVAL_MS = {"1d": 86_400_000, "1h": 3_600_000, "15m": 900_000}
RAW_COLUMNS = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
)
OUTPUT_COLUMNS = ("open_time_utc",) + RAW_COLUMNS


@dataclass(frozen=True)
class ArchiveRequest:
    kind: str
    interval: str
    date_or_month: str
    symbol: str = SYMBOL


def month_start(value: date) -> date:
    return value.replace(day=1)


def next_month(value: date) -> date:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def archive_requests(start_date: date, end_date: date, interval: str) -> list[ArchiveRequest]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date")

    result: list[ArchiveRequest] = []
    cursor = start_date
    while cursor < end_date:
        next_cursor_month = next_month(cursor)
        if cursor == month_start(cursor) and next_cursor_month <= end_date:
            result.append(ArchiveRequest("monthly", interval, cursor.strftime("%Y-%m")))
            cursor = next_cursor_month
        else:
            result.append(ArchiveRequest("daily", interval, cursor.isoformat()))
            cursor += timedelta(days=1)
    return result


def archive_filename(request: ArchiveRequest) -> str:
    return f"{request.symbol}-{request.interval}-{request.date_or_month}.zip"


def archive_url(request: ArchiveRequest) -> str:
    return (
        f"{BASE_URL}/{request.kind}/klines/{request.symbol}/{request.interval}/"
        f"{archive_filename(request)}"
    )


def parse_checksum(payload: bytes) -> str:
    match = re.search(rb"\b([a-fA-F0-9]{64})\b", payload)
    if not match:
        raise ValueError("checksum response did not contain a SHA-256 value")
    return match.group(1).decode("ascii").lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_direct_opener():
    """Return an opener that does not inherit HTTP(S)_PROXY environment settings."""
    return build_opener(ProxyHandler({}))


def download_bytes(url: str, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "btc-futures-research/1.0"})
            with build_direct_opener().open(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")


def fetch_archive(request: ArchiveRequest, raw_root: Path) -> tuple[Path, str, str]:
    url = archive_url(request)
    checksum_url = f"{url}.CHECKSUM"
    target_dir = raw_root / request.kind / request.interval
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / archive_filename(request)
    checksum_path = Path(f"{target}.CHECKSUM")

    if target.exists() and checksum_path.exists():
        expected = parse_checksum(checksum_path.read_bytes())
        actual = sha256_file(target)
        if actual == expected:
            return target, url, actual

    checksum_payload = download_bytes(checksum_url)
    expected = parse_checksum(checksum_payload)
    payload = download_bytes(url)
    temporary = Path(f"{target}.part")
    temporary.write_bytes(payload)
    actual = sha256_file(temporary)
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
    temporary.replace(target)
    checksum_path.write_bytes(checksum_payload)
    return target, url, actual


def read_kline_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        csv_members = [member for member in archive.namelist() if member.lower().endswith(".csv")]
        if len(csv_members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {len(csv_members)}")
        with archive.open(csv_members[0]) as member, io.TextIOWrapper(member, encoding="utf-8-sig", newline="") as text:
            return list(csv.reader(text))


def open_time_utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_and_validate(
    rows: Iterable[list[str]], interval: str, start_ms: int, end_ms: int
) -> list[dict[str, str]]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not row or row[0] == "open_time":
            continue
        if len(row) != len(RAW_COLUMNS):
            raise ValueError(f"expected {len(RAW_COLUMNS)} kline columns, found {len(row)}")
        open_ms = int(row[0])
        if start_ms <= open_ms < end_ms:
            item = dict(zip(RAW_COLUMNS, row, strict=True))
            item["open_time_utc"] = open_time_utc(open_ms)
            normalized.append(item)

    normalized.sort(key=lambda item: int(item["open_time_ms"]))
    expected_count = (end_ms - start_ms) // INTERVAL_MS[interval]
    if len(normalized) != expected_count:
        raise ValueError(f"missing or extra {interval} rows: expected {expected_count}, got {len(normalized)}")
    for offset, row in enumerate(normalized):
        actual = int(row["open_time_ms"])
        expected = start_ms + offset * INTERVAL_MS[interval]
        if actual != expected:
            raise ValueError(f"missing, duplicate, or misaligned {interval} row: expected {expected}, got {actual}")
    return normalized


def date_to_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def collect(end_date: date, root: Path) -> dict[str, object]:
    start_date = end_date - timedelta(days=365)
    start_ms = date_to_ms(start_date)
    end_ms = date_to_ms(end_date)
    raw_root = root / "raw"
    collected: dict[str, list[dict[str, str]]] = {}
    archives: list[dict[str, str]] = []
    for interval in INTERVALS:
        input_rows: list[list[str]] = []
        for request in archive_requests(start_date, end_date, interval):
            path, url, checksum = fetch_archive(request, raw_root)
            input_rows.extend(read_kline_rows(path))
            archives.append({"url": url, "sha256": checksum, "path": str(path.relative_to(root))})
        collected[interval] = normalize_and_validate(input_rows, interval, start_ms, end_ms)

    for interval, rows in collected.items():
        write_csv(root / "data" / f"{SYMBOL}-{interval}-365d.csv", rows)

    metadata = {
        "symbol": SYMBOL,
        "market": "USD-M perpetual futures",
        "start_date_utc": start_date.isoformat(),
        "end_date_exclusive_utc": end_date.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "row_counts": {interval: len(rows) for interval, rows in collected.items()},
        "archives": archives,
    }
    metadata_path = root / "metadata" / f"fetch-{end_date.isoformat()}.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{metadata_path}.tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata


def verify_existing(end_date: date, root: Path) -> dict[str, int]:
    start_ms = date_to_ms(end_date - timedelta(days=365))
    end_ms = date_to_ms(end_date)
    counts: dict[str, int] = {}
    for interval in INTERVALS:
        path = root / "data" / f"{SYMBOL}-{interval}-365d.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        validated = normalize_and_validate(
            [[row[column] for column in RAW_COLUMNS] for row in rows], interval, start_ms, end_ms
        )
        counts[interval] = len(validated)
    return counts


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="UTC date excluded from the 365-day range (default: today)",
    )
    parser.add_argument("--verify-only", action="store_true", help="validate existing CSVs without downloading")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    root = Path(__file__).resolve().parent
    try:
        if args.verify_only:
            print(json.dumps({"row_counts": verify_existing(args.end_date, root)}, ensure_ascii=False))
        else:
            print(json.dumps(collect(args.end_date, root), ensure_ascii=False))
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
