from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

MPL_CACHE = Path(__file__).resolve().parents[2] / ".matplotlib"
MPL_CACHE.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats
from statsmodels.tsa.stattools import acf

from .data import merge_completed_daily
from .features import add_common_features, build_daily_features, efficiency_ratio, rolling_hurst
from .artifacts import save_figure_atomic, write_csv_atomic


@dataclass
class DescriptiveResult:
    summary: dict[str, object]
    tables: list[str]
    figures: list[str]


def _write_table(frame: pd.DataFrame, name: str, tables_dir: Path, tables: list[str]) -> Path:
    path = tables_dir / name
    write_csv_atomic(frame, path)
    tables.append(name)
    return path


def _save_figure(figure: plt.Figure, name: str, figures_dir: Path, figures: list[str]) -> Path:
    path = figures_dir / name
    save_figure_atomic(figure, path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    figures.append(name)
    return path


def _ci(values: pd.Series) -> tuple[float, float]:
    clean = values.dropna().to_numpy()
    if len(clean) < 30:
        return np.nan, np.nan
    rng = np.random.default_rng(42)
    block = max(1, min(24, len(clean) // 10))
    means = []
    for _ in range(300):
        starts = rng.integers(0, max(1, len(clean) - block + 1), size=int(np.ceil(len(clean) / block)))
        sample = np.concatenate([clean[start : start + block] for start in starts])[: len(clean)]
        means.append(float(np.mean(sample)))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _pattern_stats(mask: pd.Series, forward: pd.Series, name: str) -> dict[str, object]:
    values = forward[mask].dropna()
    low, high = _ci(values)
    return {
        "pattern": name,
        "count": int(len(values)),
        "mean_forward_return": float(values.mean()) if len(values) else np.nan,
        "median_forward_return": float(values.median()) if len(values) else np.nan,
        "win_rate": float((values > 0).mean()) if len(values) else np.nan,
        "ci95_low": low,
        "ci95_high": high,
        "status": "ok" if len(values) >= 30 else "insufficient_data",
    }


def _triple_barrier(frame: pd.DataFrame, take: float, stop: float, horizon: int = 96) -> tuple[int, int, int, int]:
    upper_first = lower_first = timeout = ambiguous = 0
    starts = range(0, len(frame) - horizon, 4)
    closes = frame["close"].to_numpy()
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    for start in starts:
        upper = closes[start] * (1 + take)
        lower = closes[start] * (1 - stop)
        outcome = 0
        for offset in range(1, horizon + 1):
            hit_upper = highs[start + offset] >= upper
            hit_lower = lows[start + offset] <= lower
            if hit_upper and hit_lower:
                outcome = 2
                break
            if hit_upper:
                outcome = 1
                break
            if hit_lower:
                outcome = -1
                break
        if outcome == 1:
            upper_first += 1
        elif outcome == -1:
            lower_first += 1
        elif outcome == 2:
            ambiguous += 1
        else:
            timeout += 1
    return upper_first, lower_first, timeout, ambiguous


def _max_drawdown(log_returns: np.ndarray) -> float:
    equity = np.exp(np.cumsum(log_returns))
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    return float(np.min(equity / peak - 1))


def run_descriptive(
    frames: dict[str, pd.DataFrame],
    config: dict[str, object],
    results_dir: Path,
) -> DescriptiveResult:
    figures_dir = results_dir / "figures"
    tables_dir = results_dir / "tables"
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures: list[str] = []
    tables: list[str] = []
    summary: dict[str, object] = {}

    featured = {
        "15m": add_common_features(frames["15m"], 96),
        "1h": add_common_features(frames["1h"], 24),
        "1d": add_common_features(frames["1d"], 1),
    }

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    tail_rows = []
    extreme_rows = []
    for axis, interval in zip(axes, ("15m", "1h", "1d"), strict=True):
        values = featured[interval]["log_return"].dropna()
        stats.probplot(values, dist="norm", plot=axis)
        axis.set_title(f"{interval} return QQ")
        tail_rows.append({
            "interval": interval,
            "count": len(values),
            "skew": float(values.skew()),
            "excess_kurtosis": float(values.kurt()),
            "jarque_bera_p": float(stats.jarque_bera(values).pvalue),
        })
        low_q, high_q = values.quantile([0.03, 0.97])
        next_return = featured[interval]["log_return"].shift(-1)
        for name, mask, direction in (
            ("bottom_3pct", featured[interval]["log_return"] <= low_q, -1),
            ("top_3pct", featured[interval]["log_return"] >= high_q, 1),
        ):
            subsequent = next_return[mask].dropna()
            ci_low, ci_high = _ci(subsequent)
            continuation_low, continuation_high = _ci((np.sign(subsequent) == direction).astype(float))
            extreme_rows.append({
                "interval": interval,
                "event": name,
                "count": len(subsequent),
                "continuation_rate": float((np.sign(subsequent) == direction).mean()),
                "continuation_ci95_low": continuation_low,
                "continuation_ci95_high": continuation_high,
                "mean_next_return": float(subsequent.mean()),
                "median_next_return": float(subsequent.median()),
                "mean_ci95_low": ci_low,
                "mean_ci95_high": ci_high,
                "status": "ok" if len(subsequent) >= int(config["min_cell_count"]) else "insufficient_data",
            })
    _save_figure(fig, "01_return_qq.png", figures_dir, figures)
    _write_table(pd.DataFrame(tail_rows), "01_return_distribution.csv", tables_dir, tables)
    extreme_table = pd.DataFrame(extreme_rows)
    _write_table(extreme_table, "01_extreme_candle_aftermath.csv", tables_dir, tables)
    summary["return_kurtosis"] = {row["interval"]: row["excess_kurtosis"] for row in tail_rows}

    fig, ax = plt.subplots(figsize=(9, 4))
    for interval, nlags in (("15m", 96), ("1h", 72), ("1d", 30)):
        values = featured[interval]["atr_pct_20"].dropna()
        ax.plot(acf(values, nlags=nlags, fft=True), label=interval)
    ax.set_title("ATR% autocorrelation")
    ax.set_xlabel("lag")
    ax.legend()
    _save_figure(fig, "01_atr_autocorrelation.png", figures_dir, figures)

    intraday = featured["15m"].copy()
    utc = intraday.index
    jst = intraday.index.tz_convert("Asia/Tokyo")
    intraday["weekday_utc"] = utc.dayofweek
    intraday["hour_utc"] = utc.hour
    intraday["weekday_jst"] = jst.dayofweek
    intraday["hour_jst"] = jst.hour
    heatmaps = {
        "UTC": intraday.pivot_table(index="weekday_utc", columns="hour_utc", values="log_return", aggfunc=lambda x: float(x.std())),
        "JST": intraday.pivot_table(index="weekday_jst", columns="hour_jst", values="log_return", aggfunc=lambda x: float(x.std())),
    }
    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    heatmap_rows = []
    for axis, (timezone_name, heatmap) in zip(axes, heatmaps.items(), strict=True):
        sns.heatmap(heatmap, ax=axis, cmap="magma")
        axis.set_title(f"15m volatility by {timezone_name} weekday and hour")
        for weekday, row in heatmap.iterrows():
            for hour, value in row.items():
                heatmap_rows.append({"timezone": timezone_name, "weekday": weekday, "hour": hour, "return_std": value})
    _save_figure(fig, "02_seasonality_heatmap.png", figures_dir, figures)
    _write_table(pd.DataFrame(heatmap_rows), "02_seasonality_heatmap.csv", tables_dir, tables)

    prior_high = intraday["high"].shift(1).rolling(20).max()
    prior_low = intraday["low"].shift(1).rolling(20).min()
    up_break = intraday["high"] > prior_high
    down_break = intraday["low"] < prior_low
    single_break = up_break ^ down_break
    fake = ((up_break & (intraday["close"] <= prior_high)) | (down_break & (intraday["close"] >= prior_low))) & single_break
    breakout = pd.DataFrame({"hour_jst": intraday["hour_jst"], "breakout": single_break, "fake": fake})
    breakout_rows = []
    for hour, subset in breakout[breakout["breakout"]].groupby("hour_jst"):
        ci_low, ci_high = _ci(subset["fake"].astype(float))
        breakout_rows.append({"hour_jst": hour, "count": len(subset), "fake_rate": float(subset["fake"].mean()), "ci95_low": ci_low, "ci95_high": ci_high, "status": "ok" if len(subset) >= int(config["min_cell_count"]) else "insufficient_data"})
    breakout_table = pd.DataFrame(breakout_rows)
    _write_table(breakout_table, "02_breakout_fake_rate.csv", tables_dir, tables)

    sign = np.sign(intraday["log_return"]).replace(0, np.nan).ffill()
    groups = sign.ne(sign.shift()).cumsum()
    run_length = sign.groupby(groups).cumcount() + 1
    streak_rows = []
    next_sign = sign.shift(-1)
    for direction, label in ((1, "up"), (-1, "down")):
        for length in range(1, 7):
            mask = (sign == direction) & (run_length == length)
            sample = next_sign[mask].dropna()
            continuation = (sample == direction).astype(float)
            ci_low, ci_high = _ci(continuation)
            streak_rows.append({"direction": label, "streak_length": length, "count": len(sample), "continuation_rate": float(continuation.mean()), "ci95_low": ci_low, "ci95_high": ci_high, "status": "ok" if len(sample) >= int(config["min_cell_count"]) else "insufficient_data"})
    _write_table(pd.DataFrame(streak_rows), "02_streak_markov.csv", tables_dir, tables)

    fig, ax = plt.subplots(figsize=(9, 4))
    for interval in ("15m", "1h", "1d"):
        sns.kdeplot(featured[interval]["body_ratio"].dropna(), ax=ax, label=interval, cut=0)
    ax.set_xlim(0, 1)
    ax.set_title("Candle body / range distribution")
    ax.legend()
    _save_figure(fig, "03_candle_anatomy.png", figures_dir, figures)
    pattern_rows = []
    for interval in ("15m", "1h", "1d"):
        frame = featured[interval]
        forward = np.log(frame["close"].shift(-1) / frame["close"])
        outside = (frame["high"] > frame["high"].shift(1)) & (frame["low"] < frame["low"].shift(1))
        inside = (frame["high"] < frame["high"].shift(1)) & (frame["low"] > frame["low"].shift(1))
        long_upper = frame["upper_wick_ratio"] >= frame["upper_wick_ratio"].quantile(0.9)
        long_lower = frame["lower_wick_ratio"] >= frame["lower_wick_ratio"].quantile(0.9)
        for label, mask in (("outside", outside), ("inside", inside), ("long_upper_wick", long_upper), ("long_lower_wick", long_lower)):
            row = _pattern_stats(mask, forward, label)
            row["interval"] = interval
            pattern_rows.append(row)
    _write_table(pd.DataFrame(pattern_rows), "03_candle_patterns.csv", tables_dir, tables)

    hourly_detail = frames["1h"].loc[intraday.index.min() : intraday.index.max()].copy()
    friday = hourly_detail[(hourly_detail.index.dayofweek == 4) & (hourly_detail.index.hour == 21)]["close"]
    sunday = hourly_detail[(hourly_detail.index.dayofweek == 6) & (hourly_detail.index.hour == 22)]["close"]
    weekend_rows = []
    for time, value in sunday.items():
        prior = friday[friday.index < time]
        if len(prior):
            weekend_rows.append({"sunday_time": time.isoformat(), "friday_time": prior.index[-1].isoformat(), "binance_weekend_log_move": float(np.log(value / prior.iloc[-1]))})
    _write_table(pd.DataFrame(weekend_rows), "03_weekend_dislocation_proxy.csv", tables_dir, tables)

    fig, ax = plt.subplots(figsize=(8, 5))
    sample = intraday.dropna(subset=["log_return", "quote_asset_volume"])
    image = ax.hexbin(sample["log_return"], np.log1p(sample["quote_asset_volume"]), gridsize=60, bins="log", cmap="viridis")
    fig.colorbar(image, ax=ax, label="log count")
    ax.set_title("15m return vs log quote volume")
    ax.set_xlabel("log return")
    ax.set_ylabel("log(quote volume + 1)")
    _save_figure(fig, "04_return_volume_scatter.png", figures_dir, figures)

    typical_price = (intraday["high"] + intraday["low"] + intraday["close"]) / 3
    bins = np.linspace(typical_price.min(), typical_price.max(), 41)
    profile = pd.DataFrame({"price_bin": pd.cut(typical_price, bins=bins), "quote_volume": intraday["quote_asset_volume"]}).groupby("price_bin", observed=True)["quote_volume"].sum().reset_index()
    profile["price_mid"] = profile["price_bin"].map(lambda value: value.mid).astype(float)
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.barh(profile["price_mid"], profile["quote_volume"], height=np.diff(bins).mean() * 0.8)
    ax.set_title("Approximate 15m volume profile")
    _save_figure(fig, "04_volume_profile.png", figures_dir, figures)
    _write_table(profile.drop(columns="price_bin"), "04_volume_profile.csv", tables_dir, tables)
    volume_trigger = intraday["volume"] > 3 * intraday["volume"].shift(1).rolling(20).mean()
    direction = np.sign(intraday["log_return"])
    forward_4h = np.log(intraday["close"].shift(-16) / intraday["close"])
    volume_rows = []
    for side, label in ((1, "up"), (-1, "down")):
        price_breakout = intraday["close"] > prior_high if side == 1 else intraday["close"] < prior_low
        mask = volume_trigger & price_breakout
        values = forward_4h[mask].dropna()
        ci_low, ci_high = _ci(values)
        continuation = np.sign(values) == side
        continuation_low, continuation_high = _ci(continuation.astype(float))
        volume_rows.append({"direction": label, "count": len(values), "continuation_rate_4h": float(continuation.mean()), "continuation_ci95_low": continuation_low, "continuation_ci95_high": continuation_high, "mean_forward_4h": float(values.mean()), "mean_ci95_low": ci_low, "mean_ci95_high": ci_high, "status": "ok" if len(values) >= int(config["min_cell_count"]) else "insufficient_data"})
    _write_table(pd.DataFrame(volume_rows), "04_volume_breakout_survival.csv", tables_dir, tables)

    daily_features = build_daily_features(frames["1d"])
    daily_features["rv20_bottom_quintile"] = daily_features["rv_20"] <= daily_features["rv_20"].rolling(252, min_periods=100).quantile(0.2)
    mtf = merge_completed_daily(intraday, daily_features)
    mtf["forward_4h"] = np.log(mtf["close"].shift(-16) / mtf["close"])
    mtf_rows = []
    for slope, slope_label in ((mtf["sma20_slope"] > 0, "daily_up"), (mtf["sma20_slope"] < 0, "daily_down")):
        for signal, signal_label in ((mtf["rsi_14"] > 70, "rsi_overbought"), (mtf["rsi_14"] < 30, "rsi_oversold")):
            values = mtf.loc[slope & signal, "forward_4h"].dropna()
            ci_low, ci_high = _ci(values)
            positive_low, positive_high = _ci((values > 0).astype(float))
            mtf_rows.append({"daily_context": slope_label, "signal": signal_label, "count": len(values), "mean_forward_4h": float(values.mean()), "positive_rate": float((values > 0).mean()), "positive_ci95_low": positive_low, "positive_ci95_high": positive_high, "mean_ci95_low": ci_low, "mean_ci95_high": ci_high, "status": "ok" if len(values) >= int(config["min_cell_count"]) else "insufficient_data"})
    mtf_table = pd.DataFrame(mtf_rows)
    _write_table(mtf_table, "05_mtf_rsi_context.csv", tables_dir, tables)
    fig, ax = plt.subplots(figsize=(7, 4))
    pivot = mtf_table.pivot(index="daily_context", columns="signal", values="mean_forward_4h")
    sns.heatmap(pivot, annot=True, fmt=".4f", center=0, cmap="RdBu_r", ax=ax)
    ax.set_title("4h return by completed-daily context")
    _save_figure(fig, "05_mtf_context.png", figures_dir, figures)

    mtf["efficiency_96"] = efficiency_ratio(mtf["close"], 96)
    mtf["hurst_192"] = rolling_hurst(mtf["close"], 192)
    cross = mtf[["efficiency_96", "hurst_192", "efficiency_100", "hurst_100"]].corr().reset_index(names="metric")
    _write_table(cross, "05_fractal_cross_timeframe.csv", tables_dir, tables)
    compression = mtf["rv20_bottom_quintile"].fillna(False).astype(bool)
    compression_prior_high = mtf["high"].shift(1).rolling(20).max()
    compression_prior_low = mtf["low"].shift(1).rolling(20).min()
    compression_direction = pd.Series(0, index=mtf.index)
    compression_direction[mtf["close"] > compression_prior_high] = 1
    compression_direction[mtf["close"] < compression_prior_low] = -1
    compression_breakout = compression & compression_direction.ne(0)
    spill = mtf.loc[compression_breakout, "forward_4h"].dropna()
    spill_direction = compression_direction.reindex(spill.index)
    spill_ci_low, spill_ci_high = _ci(spill.abs())
    spill_continuation = np.sign(spill) == spill_direction
    spill_continuation_low, spill_continuation_high = _ci(spill_continuation.astype(float))
    spill_table = pd.DataFrame([{
        "count": int(len(spill)),
        "mean_abs_forward_4h": float(spill.abs().mean()) if len(spill) else np.nan,
        "directional_continuation_rate": float((np.sign(spill) == spill_direction).mean()) if len(spill) else np.nan,
        "continuation_ci95_low": spill_continuation_low,
        "continuation_ci95_high": spill_continuation_high,
        "mean_abs_ci95_low": spill_ci_low,
        "mean_abs_ci95_high": spill_ci_high,
        "status": "ok" if len(spill) >= int(config["min_cell_count"]) else "insufficient_data",
    }])
    _write_table(spill_table, "05_compression_breakout.csv", tables_dir, tables)
    summary["volatility_spillover"] = spill_table.iloc[0].to_dict()

    barrier_rows = []
    barrier_grid = (0.005, 0.01, 0.02)
    for take in barrier_grid:
        for stop in barrier_grid:
            up, down, timeout, ambiguous = _triple_barrier(intraday, take, stop)
            resolved = up + down
            outcomes = []
            for start in range(0, len(intraday) - 96, 4):
                upper = intraday["close"].iloc[start] * (1 + take)
                lower = intraday["close"].iloc[start] * (1 - stop)
                for offset in range(1, 97):
                    hit_upper = intraday["high"].iloc[start + offset] >= upper
                    hit_lower = intraday["low"].iloc[start + offset] <= lower
                    if hit_upper ^ hit_lower:
                        outcomes.append(float(hit_upper))
                        break
                    if hit_upper and hit_lower:
                        break
            probability_low, probability_high = _ci(pd.Series(outcomes, dtype=float))
            barrier_rows.append({"take_profit": take, "stop_loss": stop, "upper_first": up, "lower_first": down, "ambiguous_same_bar": ambiguous, "timeout": timeout, "resolved_count": resolved, "upper_probability_given_resolution": up / resolved if resolved else np.nan, "upper_ci95_low": probability_low, "upper_ci95_high": probability_high, "status": "ok" if resolved >= int(config["min_cell_count"]) else "insufficient_data"})
    barrier_table = pd.DataFrame(barrier_rows)
    _write_table(barrier_table, "06_triple_barrier.csv", tables_dir, tables)
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(barrier_table.pivot(index="stop_loss", columns="take_profit", values="upper_probability_given_resolution"), annot=True, fmt=".2%", cmap="viridis", ax=ax)
    ax.set_title("Upper first probability conditional on unambiguous resolution (24h)")
    _save_figure(fig, "06_triple_barrier.png", figures_dir, figures)

    fast = intraday["close"].rolling(20).mean()
    slow = intraday["close"].rolling(50).mean()
    position = np.sign(fast - slow).shift(1).fillna(0)
    cost = position.diff().abs().fillna(0) * 0.0005
    strategy_returns = (position * intraday["log_return"].fillna(0) - cost).to_numpy()
    rng = np.random.default_rng(int(config["random_seed"]))
    block = int(config["bootstrap_block_bars"])
    drawdowns = []
    ruin = 0
    for _ in range(int(config["bootstrap_samples"])):
        starts = rng.integers(0, len(strategy_returns) - block + 1, size=int(np.ceil(len(strategy_returns) / block)))
        simulated = np.concatenate([strategy_returns[start : start + block] for start in starts])[: len(strategy_returns)]
        drawdown = _max_drawdown(simulated)
        drawdowns.append(drawdown)
        ruin += int(np.r_[1.0, np.exp(np.cumsum(simulated))].min() < 0.5)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(drawdowns, bins=35)
    ax.axvline(np.median(drawdowns), color="black", linestyle="--", label="median")
    ax.set_title("Block-bootstrap one-year maximum drawdown")
    ax.legend()
    _save_figure(fig, "06_mdd_bootstrap.png", figures_dir, figures)
    risk_table = pd.DataFrame([{"bootstrap_samples": len(drawdowns), "median_mdd": float(np.median(drawdowns)), "mdd_5pct": float(np.quantile(drawdowns, 0.05)), "half_equity_probability": ruin / len(drawdowns)}])
    _write_table(risk_table, "06_mdd_bootstrap.csv", tables_dir, tables)
    summary["risk"] = risk_table.iloc[0].to_dict()

    metric_right = frames["metrics"].sort_index().reset_index()
    metric_right = metric_right.rename(columns={metric_right.columns[0]: "time"})
    derivative_left = intraday.reset_index().rename(columns={intraday.index.name or "index": "time"}).sort_values("time")
    metric_right["time"] = metric_right["time"].astype("datetime64[ns, UTC]")
    derivative_left["time"] = derivative_left["time"].astype("datetime64[ns, UTC]")
    derivatives = pd.merge_asof(
        derivative_left,
        metric_right.sort_values("time"),
        on="time",
        direction="backward",
        tolerance=pd.Timedelta(minutes=15),
    ).set_index("time")
    funding = frames["funding"][["last_funding_rate"]].reset_index()
    left = derivatives.reset_index().rename(columns={derivatives.index.name or "index": "time"}).sort_values("time")
    right = funding.rename(columns={funding.columns[0]: "time"}).sort_values("time")
    left["time"] = left["time"].astype("datetime64[ns, UTC]")
    right["time"] = right["time"].astype("datetime64[ns, UTC]")
    derivatives = pd.merge_asof(left, right, on="time", direction="backward", tolerance=pd.Timedelta(hours=8)).set_index("time")
    derivatives["oi_change_15m"] = derivatives["sum_open_interest"].pct_change()
    derivatives["volume_z_96"] = (derivatives["volume"] - derivatives["volume"].rolling(96).mean()) / derivatives["volume"].rolling(96).std()
    lower_return = derivatives["log_return"].quantile(0.03)
    derivatives["sharp_drop"] = derivatives["log_return"] <= lower_return
    derivatives["liquidation_proxy"] = derivatives["sharp_drop"] & (derivatives["oi_change_15m"] < 0) & (derivatives["volume_z_96"] > 3)
    derivatives["forward_1h"] = np.log(derivatives["close"].shift(-4) / derivatives["close"])
    derivative_rows = []
    masks = {
        "all": pd.Series(True, index=derivatives.index),
        "sharp_drop": derivatives["sharp_drop"],
        "oi_drop": derivatives["oi_change_15m"] < derivatives["oi_change_15m"].quantile(0.1),
        "funding_top_decile": derivatives["last_funding_rate"] >= derivatives["last_funding_rate"].quantile(0.9),
        "top_trader_long_ratio_top_decile": derivatives["count_toptrader_long_short_ratio"] >= derivatives["count_toptrader_long_short_ratio"].quantile(0.9),
        "global_long_ratio_top_decile": derivatives["count_long_short_ratio"] >= derivatives["count_long_short_ratio"].quantile(0.9),
        "global_long_ratio_bottom_decile": derivatives["count_long_short_ratio"] <= derivatives["count_long_short_ratio"].quantile(0.1),
        "taker_buy_ratio_top_decile": derivatives["sum_taker_long_short_vol_ratio"] >= derivatives["sum_taker_long_short_vol_ratio"].quantile(0.9),
        "taker_buy_ratio_bottom_decile": derivatives["sum_taker_long_short_vol_ratio"] <= derivatives["sum_taker_long_short_vol_ratio"].quantile(0.1),
        "liquidation_proxy": derivatives["liquidation_proxy"],
    }
    for label, mask in masks.items():
        values = derivatives.loc[mask, "forward_1h"].dropna()
        ci_low, ci_high = _ci(values)
        derivative_rows.append({"event": label, "count": len(values), "mean_forward_1h": float(values.mean()), "median_forward_1h": float(values.median()), "positive_rate": float((values > 0).mean()), "mean_ci95_low": ci_low, "mean_ci95_high": ci_high, "status": "ok" if len(values) >= int(config["min_cell_count"]) else "insufficient_data"})
    derivative_table = pd.DataFrame(derivative_rows)
    _write_table(derivative_table, "07_derivatives_events.csv", tables_dir, tables)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hexbin(derivatives["log_return"], derivatives["oi_change_15m"], gridsize=50, bins="log")
    axes[0].set_title("15m return vs OI change")
    axes[1].scatter(derivatives["last_funding_rate"], derivatives["forward_1h"], s=4, alpha=0.2)
    axes[1].set_title("Funding rate vs next 1h return")
    _save_figure(fig, "07_derivatives_events.png", figures_dir, figures)
    summary["derivatives"] = derivative_table.set_index("event").to_dict(orient="index")
    summary["cme_gap_status"] = "insufficient_data: Binance Public Data has no official CME futures series"
    summary["liquidation_status"] = "proxy_only: no official historical liquidation archive"
    return DescriptiveResult(summary=summary, tables=tables, figures=figures)
