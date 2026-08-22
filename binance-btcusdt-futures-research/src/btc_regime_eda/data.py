from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


NUMERIC_KLINE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
]


def load_kline(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required_columns = {"open_time_utc", "open_time_ms", "close_time_ms", *NUMERIC_KLINE_COLUMNS}
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ValueError(f"kline input is missing columns {sorted(missing)}: {path}")
    open_time = pd.to_numeric(frame["open_time_ms"], errors="raise")
    close_time = pd.to_numeric(frame["close_time_ms"], errors="raise")
    if open_time.isna().any() or close_time.isna().any() or not np.isfinite(open_time).all() or not np.isfinite(close_time).all():
        raise ValueError(f"kline input contains invalid timestamps: {path}")
    frame["time"] = pd.to_datetime(close_time + 1, unit="ms", utc=True)
    for column in NUMERIC_KLINE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    numeric = frame[NUMERIC_KLINE_COLUMNS]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError(f"kline input contains NaN or infinity: {path}")
    frame = frame.sort_values("time")
    duplicates = int(frame["time"].duplicated().sum())
    if duplicates:
        raise ValueError(f"kline input contains {duplicates} duplicate availability timestamps: {path}")
    frame = frame.set_index("time")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"non-monotonic kline input: {path}")
    interval = next((name for name in ("15m", "1h", "1d") if f"-{name}-" in path.name), None)
    expected = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}
    expected_ms = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000}
    if interval and (frame.index.to_series().diff().dropna() != expected[interval]).any():
        raise ValueError(f"interval gap in {interval} kline input: {path}")
    if interval and not ((close_time - open_time) == expected_ms[interval] - 1).all():
        raise ValueError(f"close_time_ms is inconsistent with {interval} open_time_ms: {path}")
    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any() or (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError(f"kline OHLC bounds are inconsistent: {path}")
    if (frame["volume"] < 0).any() or (frame["quote_asset_volume"] < 0).any() or (frame["number_of_trades"] < 0).any():
        raise ValueError(f"kline volume/trade values are negative: {path}")
    return frame


def load_inputs(root: Path, config: dict[str, object]) -> dict[str, pd.DataFrame]:
    research = root / "data" / "research"
    history_start = date.fromisoformat(str(config["history_start"]))
    detail_start = date.fromisoformat(str(config["detail_start"]))
    inclusive_end = date.fromisoformat(str(config["end_date_exclusive"])) - timedelta(days=1)
    range_suffix = f"{history_start.isoformat()}_{inclusive_end.isoformat()}"
    detail_suffix = f"{detail_start.isoformat()}_{inclusive_end.isoformat()}"
    frames = {
        "15m": load_kline(root / "data" / "BTCUSDT-15m-365d.csv"),
        "1h": load_kline(research / f"BTCUSDT-1h-{range_suffix}.csv"),
        "1d": load_kline(research / f"BTCUSDT-1d-{range_suffix}.csv"),
    }
    funding = pd.read_csv(research / f"BTCUSDT-funding-{detail_suffix}.csv")
    funding["time"] = pd.to_datetime(funding["calc_time_utc"], utc=True, format="mixed")
    funding["last_funding_rate"] = pd.to_numeric(funding["last_funding_rate"], errors="raise")
    if funding["time"].isna().any() or funding["last_funding_rate"].isna().any() or not np.isfinite(funding["last_funding_rate"]).all():
        raise ValueError("funding input contains NaN or infinity")
    if funding["time"].duplicated().any():
        raise ValueError("funding input contains duplicate timestamps")
    frames["funding"] = funding.sort_values("time").set_index("time")
    metrics = pd.read_csv(research / f"BTCUSDT-metrics-{detail_suffix}.csv")
    metrics["time"] = pd.to_datetime(metrics["create_time_utc"], utc=True, format="mixed")
    metric_columns = [
        "sum_open_interest",
        "sum_open_interest_value",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ]
    for column in metric_columns:
        metrics[column] = pd.to_numeric(metrics[column], errors="raise")
    if metrics["time"].isna().any() or metrics[metric_columns].isna().any().any() or not np.isfinite(metrics[metric_columns].to_numpy()).all():
        raise ValueError("metrics input contains NaN or infinity")
    if metrics["time"].duplicated().any():
        raise ValueError("metrics input contains duplicate timestamps")
    frames["metrics"] = metrics.sort_values("time").set_index("time")
    return frames


def quality_summary(frames: dict[str, pd.DataFrame], root: Path) -> dict[str, object]:
    expected = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}
    summary: dict[str, object] = {}
    for name, frame in frames.items():
        deltas = frame.index.to_series().diff().dropna()
        item = {
            "rows": int(len(frame)),
            "first": frame.index.min().isoformat(),
            "last": frame.index.max().isoformat(),
            "duplicates": int(frame.index.duplicated().sum()),
        }
        if name in expected:
            item["interval_gaps"] = int((deltas != expected[name]).sum())
            item["time_semantics"] = "bar_close_availability_utc"
        summary[name] = item
    manifest_path = root / "metadata" / "research-inputs.json"
    summary["input_manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))["datasets"]
    return summary


def merge_completed_daily(intraday: pd.DataFrame, daily_features: pd.DataFrame) -> pd.DataFrame:
    available = daily_features.copy()
    left = intraday.sort_index().reset_index()
    right = available.sort_index().reset_index()
    time_column = left.columns[0]
    right = right.rename(columns={right.columns[0]: time_column})
    merged = pd.merge_asof(left, right, on=time_column, direction="backward")
    return merged.set_index(time_column)
