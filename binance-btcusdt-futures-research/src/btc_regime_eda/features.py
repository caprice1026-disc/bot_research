from __future__ import annotations

import numpy as np
import pandas as pd


REGIME_FEATURES = [
    "log_return",
    "rv_24",
    "rv_168",
    "atr_pct_20",
    "mom_24",
    "mom_168",
    "volume_z_168",
    "taker_imbalance",
    "efficiency_168",
    "hurst_168",
]


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def efficiency_ratio(close: pd.Series, window: int) -> pd.Series:
    displacement = close.diff(window).abs()
    path = close.diff().abs().rolling(window).sum()
    return displacement / path.replace(0, np.nan)


def _hurst_window(values: np.ndarray) -> float:
    if len(values) < 64 or np.any(values <= 0):
        return np.nan
    log_values = np.log(values)
    lags = np.array([2, 4, 8, 16, 32], dtype=int)
    tau = np.array([np.std(log_values[lag:] - log_values[:-lag]) for lag in lags])
    if np.any(tau <= 0) or np.any(~np.isfinite(tau)):
        return np.nan
    return float(np.polyfit(np.log(lags), np.log(tau), 1)[0])


def rolling_hurst(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window, min_periods=window).apply(_hurst_window, raw=True)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    ratio = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + ratio)
    return result.mask((loss == 0) & (gain > 0), 100.0).mask((loss == 0) & (gain == 0), 50.0)


def add_common_features(frame: pd.DataFrame, periods_per_day: int) -> pd.DataFrame:
    result = frame.copy()
    result["log_return"] = np.log(result["close"]).diff()
    result["atr_20"] = true_range(result).rolling(20).mean()
    result["atr_pct_20"] = result["atr_20"] / result["close"]
    result["body_ratio"] = (result["close"] - result["open"]).abs() / (result["high"] - result["low"]).replace(0, np.nan)
    result["upper_wick_ratio"] = (result["high"] - result[["open", "close"]].max(axis=1)) / (result["high"] - result["low"]).replace(0, np.nan)
    result["lower_wick_ratio"] = (result[["open", "close"]].min(axis=1) - result["low"]) / (result["high"] - result["low"]).replace(0, np.nan)
    result["taker_imbalance"] = 2 * result["taker_buy_base_asset_volume"] / result["volume"].replace(0, np.nan) - 1
    result["rsi_14"] = rsi(result["close"])
    result["day_return"] = np.log(result["close"]).diff(periods_per_day)
    return result


def build_hourly_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = add_common_features(frame, 24)
    result["rv_24"] = result["log_return"].rolling(24).std() * np.sqrt(24)
    result["rv_168"] = result["log_return"].rolling(168).std() * np.sqrt(168)
    result["mom_24"] = np.log(result["close"] / result["close"].shift(24))
    result["mom_168"] = np.log(result["close"] / result["close"].shift(168))
    volume_mean = result["volume"].rolling(168).mean()
    volume_std = result["volume"].rolling(168).std()
    result["volume_z_168"] = (result["volume"] - volume_mean) / volume_std.replace(0, np.nan)
    result["efficiency_168"] = efficiency_ratio(result["close"], 168)
    result["hurst_168"] = rolling_hurst(result["close"], 168)
    result["sma_24"] = result["close"].rolling(24).mean()
    result["sma_168"] = result["close"].rolling(168).mean()
    rolling_mean = result["close"].rolling(24).mean()
    rolling_std = result["close"].rolling(24).std()
    result["zscore_24"] = (result["close"] - rolling_mean) / rolling_std.replace(0, np.nan)
    result["forward_return_1h"] = result["log_return"].shift(-1)
    result["forward_return_24h"] = np.log(result["close"].shift(-24) / result["close"])
    result["forward_rv_24h"] = result["log_return"].shift(-1).rolling(24).std().shift(-23) * np.sqrt(24)
    future_lows = pd.concat([result["low"].shift(-step) for step in range(1, 25)], axis=1)
    future_highs = pd.concat([result["high"].shift(-step) for step in range(1, 25)], axis=1)
    full_future_lows = future_lows.where(future_lows.notna().sum(axis=1).eq(24))
    full_future_highs = future_highs.where(future_highs.notna().sum(axis=1).eq(24))
    result["forward_mae_24h"] = (full_future_lows.min(axis=1) / result["close"] - 1).clip(upper=0)
    result["forward_mfe_24h"] = (full_future_highs.max(axis=1) / result["close"] - 1).clip(lower=0)
    return result



def build_daily_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = add_common_features(frame, 1)
    result["sma_20"] = result["close"].rolling(20).mean()
    result["sma20_slope"] = result["sma_20"].pct_change(5)
    result["rv_20"] = result["log_return"].rolling(20).std() * np.sqrt(20)
    result["efficiency_100"] = efficiency_ratio(result["close"], 100)
    result["hurst_100"] = rolling_hurst(result["close"], 100)
    return result[["sma_20", "sma20_slope", "rv_20", "efficiency_100", "hurst_100", "atr_pct_20"]]
