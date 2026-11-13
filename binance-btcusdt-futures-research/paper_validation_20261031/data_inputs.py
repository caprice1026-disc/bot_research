"""Public, checksum-verified inputs; no credentials or order endpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data" / "paper_validation_20261031"
OUT = HERE / "results"
sys.path.insert(0, str(ROOT))
from download_binance_klines import parse_checksum, sha256_file


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def download(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "paper-validation-research/1.0"}), timeout=45) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def fetch_verified(kind, month):
    filename = f"BTCUSDT-{'fundingRate' if kind == 'fundingRate' else '1m'}-{month}.zip"
    middle = "" if kind == "fundingRate" else "1m/"
    frequency = "daily" if len(month) == 10 else "monthly"
    url = f"https://data.binance.vision/data/futures/um/{frequency}/{kind}/BTCUSDT/{middle}{filename}"
    path = DATA / "raw" / kind / filename
    checksum_path = path.with_suffix(".zip.CHECKSUM")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and checksum_path.exists() and sha256_file(path) == parse_checksum(checksum_path.read_bytes()):
        return dict(kind=kind, month=month, url=url, path=str(path.relative_to(ROOT)), sha256=sha256_file(path), status="verified")
    checksum = download(url + ".CHECKSUM")
    payload = download(url)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != parse_checksum(checksum):
        raise ValueError(f"checksum mismatch: {url}")
    tmp = path.with_suffix(".zip.part")
    tmp.write_bytes(payload)
    tmp.replace(path)
    checksum_path.write_bytes(checksum)
    return dict(kind=kind, month=month, url=url, path=str(path.relative_to(ROOT)), sha256=digest, status="verified")


def collect(config):
    months = pd.date_range(config["start"], config["end_exclusive"], freq="MS", inclusive="left").strftime("%Y-%m")
    tasks = [(kind, month) for kind in ("klines", "markPriceKlines", "fundingRate") for month in months]
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_verified, *task): task for task in tasks}
        for future in as_completed(futures):
            kind, month = futures[future]
            try:
                records.append(future.result())
                print(f"verified {kind} {month}", flush=True)
            except Exception as error:
                failures.append(dict(kind=kind, month=month, error=str(error)))
                print(f"failed {kind} {month}: {error}", flush=True)
            write_json(OUT / "download_manifest.json", dict(archives=sorted(records, key=lambda x:(x["kind"],x["month"])), failures=failures))
    if failures:
        raise RuntimeError(f"{len(failures)} archives unavailable; no missing data imputed")
    # Monthly mark archives can omit a whole day even with a valid checksum.
    # Repair only from separately checksummed official daily files; never interpolate.
    mark_parts = [read_archive(ROOT / r["path"]) for r in records if r["kind"] == "markPriceKlines"]
    observed = pd.concat(mark_parts).open_time.to_numpy(dtype=np.int64)
    expected = np.arange(pd.Timestamp(config["start"], tz="UTC").value//10**6,
                         pd.Timestamp(config["end_exclusive"], tz="UTC").value//10**6, 60000)
    missing = np.setdiff1d(expected, observed)
    for day in sorted(set(pd.to_datetime(missing, unit="ms", utc=True).strftime("%Y-%m-%d"))):
        record = fetch_verified("markPriceKlines", day)
        records.append(record)
        print(f"verified daily mark repair {day}", flush=True)
    write_json(OUT / "download_manifest.json", dict(archives=sorted(records, key=lambda x:(x["kind"],x["month"])), failures=failures))
    return prepare(config)


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = [x for x in archive.namelist() if x.endswith(".csv")]
        if len(names) != 1:
            raise ValueError("expected one CSV")
        with archive.open(names[0]) as handle:
            return pd.read_csv(handle)


def prepare(config):
    manifest = json.loads((OUT / "download_manifest.json").read_text(encoding="utf-8"))
    if manifest["failures"]:
        raise ValueError("incomplete archives")
    frames = {}
    for kind in ("klines", "markPriceKlines", "fundingRate"):
        parts = []
        for record in manifest["archives"]:
            if record["kind"] != kind:
                continue
            path = ROOT / record["path"]
            if sha256_file(path) != record["sha256"]:
                raise ValueError("input archive changed")
            parts.append(read_archive(path))
        frame = pd.concat(parts, ignore_index=True)
        time_col = "calc_time" if kind == "fundingRate" else "open_time"
        # A daily fallback may overlap intact monthly rows. Require exact agreement.
        duplicates = frame[frame.duplicated(time_col, keep=False)]
        if len(duplicates) and len(duplicates.drop_duplicates()) != duplicates[time_col].nunique():
            raise ValueError("conflicting monthly/daily observations")
        frames[kind] = frame.drop_duplicates(time_col).sort_values(time_col).reset_index(drop=True)
    start = pd.Timestamp(config["start"], tz="UTC").value // 10**6
    end = pd.Timestamp(config["end_exclusive"], tz="UTC").value // 10**6
    expected = np.arange(start, end, 60000, dtype=np.int64)
    quality = {"status": "complete", "start": config["start"], "end_exclusive": config["end_exclusive"], "symbol": config["symbol"], "archives": len(manifest["archives"])}
    output = {}
    for kind in ("klines", "markPriceKlines"):
        frame = frames[kind]
        frame.columns = [x.lower() for x in frame.columns]
        timestamp = frame["open_time"].to_numpy(dtype=np.int64)
        if not np.array_equal(timestamp, expected):
            raise ValueError(f"{kind}: missing, duplicate, unordered or unexpected timestamps")
        if not np.array_equal(frame.close_time.to_numpy(dtype=np.int64) + 1, expected + 60000):
            raise ValueError("unexpected candle boundaries")
        for col in ("open", "high", "low", "close"):
            arr = frame[col].to_numpy(dtype=float)
            if not np.all(np.isfinite(arr) & (arr > 0)):
                raise ValueError("invalid price")
            output[("mark_" if kind == "markPriceKlines" else "") + col] = arr
        if np.any(frame.high < frame[["open","close","low"]].max(axis=1)) or np.any(frame.low > frame[["open","close","high"]].min(axis=1)):
            raise ValueError("inconsistent OHLC")
        if kind == "klines":
            output["volume"] = frame.volume.to_numpy(dtype=float)
        quality[kind] = dict(rows=len(frame), missing=0, duplicate=0)
    funding = frames["fundingRate"]
    timestamp = funding.calc_time.to_numpy(dtype=np.int64)
    boundary = timestamp // 28800000 * 28800000
    if not np.array_equal(boundary, np.arange(start, end, 28800000)) or np.any(timestamp - boundary > 1000):
        raise ValueError("missing or unsupported funding schedule; cannot assume zero")
    rate = funding.last_funding_rate.to_numpy(dtype=float)
    if not np.all(np.isfinite(rate)):
        raise ValueError("invalid funding rate")
    if "funding_interval_hours" in funding and not np.all(funding.funding_interval_hours == 8):
        raise ValueError("non-eight-hour funding")
    output["timestamp"] = expected
    output["funding_rate"] = np.zeros(len(expected))
    output["funding_event"] = np.zeros(len(expected), dtype=bool)
    positions = (boundary-start)//60000
    output["funding_rate"][positions] = rate
    output["funding_event"][positions] = True
    quality["funding"] = dict(rows=len(rate), missing=0, duplicate=0, max_timestamp_offset_ms=int((timestamp-boundary).max()), settlement_price="archived mark-price 1m open at normalized boundary (approximation)")
    path = DATA / "market_1m.npz"
    np.savez_compressed(path, **output)
    quality["normalized_sha256"] = sha256_file(path)
    quality["source_manifest_sha256"] = sha256_file(OUT / "download_manifest.json")
    write_json(OUT / "data_quality.json", quality)
    print(json.dumps(quality), flush=True)
    return quality


def fetch_upstream(config):
    revision = config["upstream_revision"]
    path = DATA / f"Reward-alignment-{revision}.zip"
    DATA.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        payload = download(f"https://codeload.github.com/DataismLab/Reward-alignment/zip/{revision}")
        path.write_bytes(payload)
    target = DATA / "upstream"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        root = target.resolve()
        for member in archive.infolist():
            resolved = (root / member.filename).resolve()
            if root != resolved and root not in resolved.parents:
                raise ValueError("unsafe archive path")
        if not (target / f"Reward-alignment-{revision}" / "README.md").exists():
            archive.extractall(target)
    write_json(OUT / "upstream_provenance.json", dict(url="https://github.com/DataismLab/Reward-alignment", revision=revision, archive_sha256=sha256_file(path), path=str((target / f"Reward-alignment-{revision}").relative_to(ROOT))))
    print(f"upstream saved: {target / f'Reward-alignment-{revision}'}", flush=True)
