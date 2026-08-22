from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
import zipfile

import pandas as pd

from .artifacts import write_csv_atomic, write_json_atomic


@dataclass(frozen=True)
class InputArchive:
    dataset: str
    period: str
    url: str
    local_path: str
    sha256: str


def month_starts(start: date, end: date) -> Iterable[date]:
    cursor = start.replace(day=1)
    while cursor < end:
        yield cursor
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def _generic_fetch(url: str, target: Path, download_bytes, parse_checksum, sha256_file) -> tuple[Path, str]:
    checksum_url = f"{url}.CHECKSUM"
    checksum_path = Path(f"{target}.CHECKSUM")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and checksum_path.exists():
        expected = parse_checksum(checksum_path.read_bytes())
        actual = sha256_file(target)
        if expected == actual:
            return target, actual
    checksum_payload = download_bytes(checksum_url)
    expected = parse_checksum(checksum_payload)
    payload = download_bytes(url)
    temporary = Path(f"{target}.part")
    temporary.write_bytes(payload)
    actual = sha256_file(temporary)
    if expected != actual:
        raise RuntimeError(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
    temporary.replace(target)
    checksum_path.write_bytes(checksum_payload)
    return target, actual


def _read_zip_csv(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected exactly one CSV in {path}, found {len(members)}")
        with archive.open(members[0]) as raw:
            return pd.read_csv(raw)


def _validate_time_index(
    frame: pd.DataFrame,
    column: str,
    expected_seconds: int | None,
    *,
    allow_gaps: bool = False,
) -> dict[str, object]:
    timestamps = pd.to_datetime(frame[column], utc=True)
    duplicates = int(timestamps.duplicated().sum())
    if duplicates:
        raise ValueError(f"{column} contains {duplicates} duplicate timestamps")
    if not timestamps.is_monotonic_increasing:
        raise ValueError(f"{column} is not monotonic increasing")
    gaps = 0
    missing_intervals = 0
    if expected_seconds is not None and len(timestamps) > 1:
        deltas = timestamps.diff().dropna()
        expected_delta = pd.Timedelta(seconds=expected_seconds)
        gaps = int((deltas != expected_delta).sum())
        missing_intervals = int(
            sum(max(0, int(round(delta / expected_delta)) - 1) for delta in deltas if delta > expected_delta)
        )
        if gaps and not allow_gaps:
            raise ValueError(f"{column} contains {gaps} interval gaps")
    return {
        "row_count": int(len(frame)),
        "first_time": timestamps.iloc[0].isoformat() if len(frame) else None,
        "last_time": timestamps.iloc[-1].isoformat() if len(frame) else None,
        "duplicates": duplicates,
        "interval_gaps": gaps,
        "missing_intervals": missing_intervals,
    }


def collect_research_inputs(config: dict[str, object], root: Path) -> dict[str, object]:
    import sys

    sys.path.insert(0, str(root))
    from download_binance_klines import (
        INTERVAL_MS,
        RAW_COLUMNS,
        archive_requests,
        archive_url,
        date_to_ms,
        download_bytes,
        fetch_archive,
        normalize_and_validate,
        parse_checksum,
        read_kline_rows,
        sha256_file,
        write_csv,
    )

    history_start = date.fromisoformat(str(config["history_start"]))
    detail_start = date.fromisoformat(str(config["detail_start"]))
    end_date = date.fromisoformat(str(config["end_date_exclusive"]))
    inclusive_end = end_date - timedelta(days=1)
    raw_root = root / "raw"
    data_root = root / "data" / "research"
    data_root.mkdir(parents=True, exist_ok=True)
    archive_records: list[InputArchive] = []
    datasets: dict[str, dict[str, object]] = {}

    for interval in ("1h", "1d"):
        all_rows: list[list[str]] = []
        for number, request in enumerate(archive_requests(history_start, end_date, interval), start=1):
            path, url, checksum = fetch_archive(request, raw_root)
            all_rows.extend(read_kline_rows(path))
            archive_records.append(InputArchive(f"kline_{interval}", request.date_or_month, url, str(path.relative_to(root)), checksum))
            if number % 25 == 0:
                print(f"{interval}: verified {number} archives", flush=True)
        rows = normalize_and_validate(all_rows, interval, date_to_ms(history_start), date_to_ms(end_date))
        output = data_root / f"BTCUSDT-{interval}-{history_start.isoformat()}_{inclusive_end.isoformat()}.csv"
        write_csv(output, rows)
        datasets[f"kline_{interval}"] = _validate_time_index(
            pd.DataFrame(rows), "open_time_utc", INTERVAL_MS[interval] // 1000
        ) | {"status": "complete", "path": str(output.relative_to(root))}

    funding_frames: list[pd.DataFrame] = []
    funding_missing_periods: list[str] = []
    funding_end_month = end_date.replace(day=1)
    for month in month_starts(detail_start, funding_end_month):
        period = month.strftime("%Y-%m")
        filename = f"BTCUSDT-fundingRate-{period}.zip"
        url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/{filename}"
        target = raw_root / "monthly" / "fundingRate" / filename
        try:
            path, checksum = _generic_fetch(url, target, download_bytes, parse_checksum, sha256_file)
        except HTTPError as error:
            if error.code != 404:
                raise
            funding_missing_periods.append(period)
            continue
        frame = _read_zip_csv(path)
        funding_frames.append(frame)
        archive_records.append(InputArchive("funding_rate", period, url, str(path.relative_to(root)), checksum))
    if not funding_frames:
        raise ValueError("no official funding-rate archive was available for the requested range")
    funding = pd.concat(funding_frames, ignore_index=True)
    funding["calc_time_utc"] = pd.to_datetime(funding["calc_time"], unit="ms", utc=True)
    funding = funding[(funding["calc_time_utc"] >= pd.Timestamp(detail_start, tz="UTC")) & (funding["calc_time_utc"] < pd.Timestamp(end_date, tz="UTC"))]
    funding = funding.sort_values("calc_time_utc")
    funding_output = data_root / f"BTCUSDT-funding-{detail_start.isoformat()}_{inclusive_end.isoformat()}.csv"
    write_csv_atomic(funding, funding_output)
    funding_stats = _validate_time_index(funding, "calc_time_utc", None)
    funding_expected_end = pd.Timestamp(end_date, tz="UTC") - pd.Timedelta(hours=8)
    funding_status = "complete" if pd.Timestamp(funding_stats["last_time"]) >= funding_expected_end else "insufficient_data"
    datasets["funding_rate"] = funding_stats | {
        "status": funding_status,
        "path": str(funding_output.relative_to(root)),
        "note": "monthly archive publication can lag the analysis end date",
        "missing_periods": funding_missing_periods,
    }

    metric_frames: list[pd.DataFrame] = []
    metrics_missing_periods: list[str] = []
    cursor = detail_start
    metric_count = 0
    while cursor < end_date:
        period = cursor.isoformat()
        filename = f"BTCUSDT-metrics-{period}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/{filename}"
        target = raw_root / "daily" / "metrics" / filename
        try:
            path, checksum = _generic_fetch(url, target, download_bytes, parse_checksum, sha256_file)
        except HTTPError as error:
            if error.code != 404:
                raise
            metrics_missing_periods.append(period)
            cursor += timedelta(days=1)
            metric_count += 1
            continue
        metric_frames.append(_read_zip_csv(path))
        metric_frames[-1]["source_archive_date"] = period
        archive_records.append(InputArchive("metrics", period, url, str(path.relative_to(root)), checksum))
        cursor += timedelta(days=1)
        metric_count += 1
        if metric_count % 50 == 0:
            print(f"metrics: verified {metric_count} daily archives", flush=True)
    if not metric_frames:
        raise ValueError("no official metrics archive was available for the requested range")
    metrics = pd.concat(metric_frames, ignore_index=True)
    metrics["create_time_utc_raw"] = pd.to_datetime(metrics["create_time"], utc=True)
    duplicate_raw = metrics[metrics["create_time_utc_raw"].duplicated(keep=False)].copy()
    boundary_duplicates_removed = 0
    if not duplicate_raw.empty:
        matching_archive_day = duplicate_raw["source_archive_date"] == duplicate_raw["create_time_utc_raw"].dt.date.astype(str)
        if not matching_archive_day.groupby(duplicate_raw["create_time_utc_raw"]).sum().eq(1).all():
            raise ValueError("metrics contains conflicting duplicate timestamps that cannot be attributed to a daily archive boundary")
        metrics = metrics.sort_values(["create_time_utc_raw", "source_archive_date"])
        metrics = metrics.loc[~metrics.duplicated("create_time_utc_raw", keep="last")].copy()
        boundary_duplicates_removed = int(len(duplicate_raw) // 2)
    rounded = metrics["create_time_utc_raw"].dt.round("5min")
    adjustment_seconds = (rounded - metrics["create_time_utc_raw"]).dt.total_seconds().abs()
    if (adjustment_seconds > 5).any():
        raise ValueError("metrics contains timestamps more than five seconds off the five-minute grid")
    metrics["create_time_utc"] = rounded
    metrics = metrics.sort_values("create_time_utc")
    metrics_output = data_root / f"BTCUSDT-metrics-{detail_start.isoformat()}_{inclusive_end.isoformat()}.csv"
    write_csv_atomic(metrics, metrics_output)
    metrics_stats = _validate_time_index(metrics, "create_time_utc", 300, allow_gaps=True)
    datasets["metrics"] = metrics_stats | {
        "status": "success_with_warnings" if metrics_stats["missing_intervals"] or metrics_missing_periods else "complete",
        "path": str(metrics_output.relative_to(root)),
        "timestamp_adjustments_within_5s": int((adjustment_seconds > 0).sum()),
        "cross_archive_boundary_duplicates_removed": boundary_duplicates_removed,
        "note": "official source gaps are preserved without interpolation",
        "missing_periods": metrics_missing_periods,
    }

    manifest = {
        "symbol": "BTCUSDT",
        "market": "USD-M perpetual futures",
        "history_start": history_start.isoformat(),
        "detail_start": detail_start.isoformat(),
        "end_date_exclusive": end_date.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "datasets": datasets,
        "archives": [asdict(record) for record in archive_records],
    }
    manifest_payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    manifest["manifest_sha256"] = hashlib.sha256(manifest_payload.encode("utf-8")).hexdigest()
    output_manifest = root / "metadata" / "research-inputs.json"
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest, output_manifest)
    return manifest
