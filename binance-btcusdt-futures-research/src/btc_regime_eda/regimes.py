from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import os
from pathlib import Path

MPL_CACHE = Path(__file__).resolve().parents[2] / ".matplotlib"
MPL_CACHE.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE))
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from hmmlearn.hmm import GaussianHMM
import numpy as np
import pandas as pd
import ruptures as rpt
from sklearn.metrics import adjusted_rand_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import RobustScaler

from .features import REGIME_FEATURES
from .artifacts import save_figure_atomic, write_csv_atomic


@dataclass
class RegimeResult:
    summary: dict[str, object]
    selected_hmm_states: int
    selected_gmm_states: int
    tables: list[str]
    figures: list[str]


def state_name_mapping(states: np.ndarray, frame: pd.DataFrame, count: int) -> dict[int, str]:
    stats = []
    for state in range(count):
        subset = frame.iloc[np.flatnonzero(states == state)]
        stats.append({
            "state": state,
            "return": float(subset["log_return"].mean()),
            "vol": float(subset["rv_24"].mean()),
            "momentum": float(subset["mom_168"].mean()),
        })
    table = pd.DataFrame(stats).set_index("state")
    remaining = set(table.index)
    mapping: dict[int, str] = {}
    negative = table[table["return"] < 0]
    stress = int(negative["vol"].idxmax()) if not negative.empty else int(table["vol"].idxmax())
    mapping[stress] = "bear_stress" if not negative.empty else "stress_proxy"
    remaining.remove(stress)
    if remaining:
        candidates = table.loc[list(remaining)]
        positive = candidates[(candidates["return"] > 0) & (candidates["momentum"] > 0)]
        bull = int(positive["momentum"].idxmax()) if not positive.empty else int(candidates["momentum"].idxmax())
        mapping[bull] = "bull_trend" if not positive.empty else "momentum_proxy"
        remaining.remove(bull)
    if remaining:
        quiet = int(table.loc[list(remaining), "vol"].idxmin())
        mapping[quiet] = "quiet_range"
        remaining.remove(quiet)
    for state in remaining:
        mapping[int(state)] = "volatile_transition"
    return mapping


def fit_hmm(values: np.ndarray, states: int, seed: int, iterations: int = 80) -> GaussianHMM:
    model = GaussianHMM(
        n_components=states,
        covariance_type="diag",
        n_iter=iterations,
        tol=1e-3,
        min_covar=1e-5,
        random_state=seed,
        implementation="log",
    )
    model.fit(values)
    return model


def has_minimum_occupancy(states: np.ndarray, state_count: int, minimum: float = 0.05) -> bool:
    return bool(len(states) and np.bincount(states, minlength=state_count).min() / len(states) >= minimum)


def _block_mean_ci(values: pd.Series) -> tuple[float, float]:
    clean = values.dropna().to_numpy()
    if len(clean) < 30:
        return np.nan, np.nan
    rng = np.random.default_rng(42)
    block = min(24, max(1, len(clean) // 10))
    means = []
    for _ in range(300):
        starts = rng.integers(0, max(1, len(clean) - block + 1), size=int(np.ceil(len(clean) / block)))
        sample = np.concatenate([clean[start : start + block] for start in starts])[: len(clean)]
        means.append(float(sample.mean()))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _state_statistics(frame: pd.DataFrame, labels: pd.Series, model_name: str) -> pd.DataFrame:
    rows = []
    for label, subset in frame.groupby(labels):
        ci_low, ci_high = _block_mean_ci(subset["forward_return_1h"])
        rows.append({
            "model": model_name,
            "state": label,
            "count": int(len(subset)),
            "occupancy": float(len(subset) / len(frame)),
            "mean_hourly_return": float(subset["log_return"].mean()),
            "mean_rv_24": float(subset["rv_24"].mean()),
            "mean_momentum_168": float(subset["mom_168"].mean()),
            "next_1h_return": float(subset["forward_return_1h"].mean()),
            "next_1h_ci95_low": ci_low,
            "next_1h_ci95_high": ci_high,
            "next_24h_return": float(subset["forward_return_24h"].mean()),
            "next_24h_vol": float(subset["forward_rv_24h"].mean()),
        })
    return pd.DataFrame(rows)


def run_regime_analysis(
    hourly_features: pd.DataFrame,
    config: dict[str, object],
    results_dir: Path,
) -> RegimeResult:
    tables_dir = results_dir / "tables"
    figures_dir = results_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables: list[str] = []
    figures: list[str] = []
    frame = hourly_features.dropna(subset=REGIME_FEATURES).copy()
    values = frame[REGIME_FEATURES].to_numpy()
    scaler = RobustScaler().fit(values)
    scaled = scaler.transform(values)
    split = int(len(frame) * 0.8)
    candidate_rows = []
    hmm_candidates: dict[int, GaussianHMM] = {}
    gmm_candidates: dict[int, GaussianMixture] = {}
    for state_count in config["candidate_states"]:
        state_count = int(state_count)
        validation_scaler = RobustScaler().fit(values[:split])
        train = validation_scaler.transform(values[:split])
        validation = validation_scaler.transform(values[split:])
        hmm_validation = fit_hmm(train, state_count, int(config["random_seed"]), 60)
        holdout_score = float(hmm_validation.score(validation) / len(validation))
        validation_states = hmm_validation.predict(validation)
        min_occupancy = float(np.bincount(validation_states, minlength=state_count).min() / len(validation_states))
        hmm = fit_hmm(scaled, state_count, int(config["random_seed"]), 80)
        hmm_states = hmm.predict(scaled)
        hmm_full_min_occupancy = float(np.bincount(hmm_states, minlength=state_count).min() / len(hmm_states))
        gmm = GaussianMixture(n_components=state_count, covariance_type="full", n_init=5, random_state=int(config["random_seed"])).fit(scaled)
        gmm_states = gmm.predict(scaled)
        gmm_min_occupancy = float(np.bincount(gmm_states, minlength=state_count).min() / len(gmm_states))
        gmm_seed_states = []
        for seed in config["stability_seeds"]:
            seeded = GaussianMixture(
                n_components=state_count,
                covariance_type="full",
                n_init=1,
                random_state=int(seed),
            ).fit(scaled)
            gmm_seed_states.append(seeded.predict(scaled))
        gmm_seed_ari = [adjusted_rand_score(left, right) for left, right in combinations(gmm_seed_states, 2)]
        candidate_rows.append({
            "states": state_count,
            "hmm_holdout_loglik_per_row": holdout_score,
            "hmm_min_occupancy": min_occupancy,
            "hmm_converged": bool(hmm.monitor_.converged),
            "hmm_full_min_occupancy": hmm_full_min_occupancy,
            "gmm_bic": float(gmm.bic(scaled)),
            "gmm_min_occupancy": gmm_min_occupancy,
            "gmm_converged": bool(gmm.converged_),
            "gmm_seed_mean_ari": float(np.mean(gmm_seed_ari)),
        })
        hmm_candidates[state_count] = hmm
        gmm_candidates[state_count] = gmm
    candidate_table = pd.DataFrame(candidate_rows)
    write_csv_atomic(candidate_table, tables_dir / "08_regime_model_selection.csv")
    tables.append("08_regime_model_selection.csv")
    valid_hmm = candidate_table[(candidate_table["hmm_min_occupancy"] >= 0.05) & (candidate_table["hmm_full_min_occupancy"] >= 0.05) & candidate_table["hmm_converged"]]
    if valid_hmm.empty:
        raise ValueError("no converged HMM candidate passed the 5% occupancy gate")
    selected_hmm = int(valid_hmm.sort_values("hmm_holdout_loglik_per_row", ascending=False).iloc[0]["states"])
    valid_gmm = candidate_table[(candidate_table["gmm_min_occupancy"] >= 0.05) & candidate_table["gmm_converged"]]
    if valid_gmm.empty:
        raise ValueError("no converged GMM candidate passed the 5% occupancy gate")
    selected_gmm = int(valid_gmm.sort_values("gmm_bic").iloc[0]["states"])

    stability_states = []
    stability_scores = []
    best_model = hmm_candidates[selected_hmm]
    best_score = best_model.score(scaled)
    for seed in config["stability_seeds"]:
        model = fit_hmm(scaled, selected_hmm, int(seed), 80)
        if not model.monitor_.converged:
            continue
        states = model.predict(scaled)
        if not has_minimum_occupancy(states, selected_hmm):
            continue
        stability_states.append(states)
        score = float(model.score(scaled))
        if score > best_score:
            best_model = model
            best_score = score
    for left, right in combinations(stability_states, 2):
        stability_scores.append(float(adjusted_rand_score(left, right)))
    if not stability_states:
        raise ValueError("no converged stable HMM fit passed the 5% occupancy gate")
    raw_hmm = best_model.predict(scaled)
    hmm_mapping = state_name_mapping(raw_hmm, frame, selected_hmm)
    hmm_labels = pd.Series([hmm_mapping[int(state)] for state in raw_hmm], index=frame.index, name="hmm_regime")
    gmm_model = gmm_candidates[selected_gmm]
    raw_gmm = gmm_model.predict(scaled)
    gmm_mapping = state_name_mapping(raw_gmm, frame, selected_gmm)
    gmm_labels = pd.Series([gmm_mapping[int(state)] for state in raw_gmm], index=frame.index, name="gmm_regime")
    state_table = pd.concat([
        _state_statistics(frame, hmm_labels, "HMM"),
        _state_statistics(frame, gmm_labels, "GMM"),
    ], ignore_index=True)
    write_csv_atomic(state_table, tables_dir / "08_regime_state_statistics.csv")
    tables.append("08_regime_state_statistics.csv")

    dwell_rows = []
    for model_name, model_labels in (("HMM", hmm_labels), ("GMM", gmm_labels)):
        for label in model_labels.unique():
            mask = model_labels == label
            groups = mask.ne(mask.shift()).cumsum()
            durations = mask.groupby(groups).sum()
            durations = durations[durations > 0]
            dwell_rows.append({"model": model_name, "state": label, "episodes": len(durations), "mean_hours": float(durations.mean()), "median_hours": float(durations.median()), "max_hours": int(durations.max())})
    write_csv_atomic(pd.DataFrame(dwell_rows), tables_dir / "08_regime_dwell_times.csv")
    tables.append("08_regime_dwell_times.csv")

    detail = frame.loc[frame.index >= pd.Timestamp(str(config["detail_start"]), tz="UTC")]
    detail_labels = hmm_labels.reindex(detail.index)
    color_map = {"bull_trend": "#2ca02c", "momentum_proxy": "#9467bd", "bear_stress": "#d62728", "stress_proxy": "#8c564b", "quiet_range": "#1f77b4", "volatile_transition": "#ff7f0e"}
    fig, ax = plt.subplots(figsize=(14, 5))
    for label in detail_labels.dropna().unique():
        subset = detail[detail_labels == label]
        ax.scatter(subset.index, subset["close"], s=5, color=color_map[label], label=label)
    ax.set_yscale("log")
    ax.set_title("Descriptive full-sample HMM regimes (not walk-forward)")
    ax.legend(ncol=4)
    save_figure_atomic(fig, figures_dir / "08_descriptive_hmm_regimes.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append("08_descriptive_hmm_regimes.png")

    daily = frame[["log_return", "rv_24"]].resample("1D").agg({"log_return": "sum", "rv_24": "mean"}).dropna()
    daily_scaled = RobustScaler().fit_transform(daily)
    change_rows = []
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(daily.index, np.log(frame["close"]).resample("1D").last().reindex(daily.index), color="black")
    for multiplier, color in ((1.0, "#ffcc00"), (3.0, "#ff7f0e"), (10.0, "#d62728")):
        penalty = float(np.log(len(daily)) * multiplier)
        endpoints = rpt.Pelt(model="l2", min_size=14, jump=1).fit(daily_scaled).predict(pen=penalty)
        for endpoint in endpoints[:-1]:
            when = daily.index[endpoint - 1]
            change_rows.append({"penalty_multiplier": multiplier, "change_time": when.isoformat()})
            if multiplier == 3.0:
                ax.axvline(when, color=color, alpha=0.25)
    ax.set_title("Offline PELT change points (descriptive only)")
    save_figure_atomic(fig, figures_dir / "08_pelt_change_points.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append("08_pelt_change_points.png")
    write_csv_atomic(pd.DataFrame(change_rows), tables_dir / "08_pelt_change_points.csv")
    tables.append("08_pelt_change_points.csv")

    summary = {
        "selected_hmm_states": selected_hmm,
        "selected_gmm_states": selected_gmm,
        "hmm_seed_mean_ari": float(np.mean(stability_scores)),
        "hmm_seed_min_ari": float(np.min(stability_scores)),
        # EM's final floating-point accumulator can vary by roughly 1e-11
        # across otherwise identical threaded BLAS runs.  This is a reported
        # diagnostic, not a model input, so persist a stable precision.
        "hmm_full_sample_loglik_per_row": round(float(best_score / len(frame)), 10),
        "pelt_primary_change_points": int(sum(row["penalty_multiplier"] == 3.0 for row in change_rows)),
        "descriptive_warning": "full-sample regimes use future distribution information and are not trading signals",
    }
    return RegimeResult(summary, selected_hmm, selected_gmm, tables, figures)
