from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path

MPL_CACHE = Path(__file__).resolve().parents[2] / ".matplotlib"
MPL_CACHE.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from hmmlearn.hmm import GaussianHMM
import numpy as np
import pandas as pd
from scipy.special import logsumexp
from scipy.stats import multivariate_normal
from sklearn.metrics import adjusted_rand_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import RobustScaler
from threadpoolctl import threadpool_limits

from .features import REGIME_FEATURES
from .regimes import fit_hmm, state_name_mapping
from .regimes import has_minimum_occupancy
from .artifacts import save_figure_atomic, write_csv_atomic


@dataclass
class WalkForwardResult:
    summary: dict[str, object]
    tables: list[str]
    figures: list[str]


def _block_mean_ci(values: pd.Series, seed: int = 42) -> tuple[float, float]:
    clean = values.dropna().to_numpy()
    if len(clean) < 30:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    block = min(24, max(1, len(clean) // 10))
    means = []
    for _ in range(300):
        starts = rng.integers(0, max(1, len(clean) - block + 1), size=int(np.ceil(len(clean) / block)))
        sample = np.concatenate([clean[start : start + block] for start in starts])[: len(clean)]
        means.append(float(sample.mean()))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _emission_log_likelihood(model: GaussianHMM, values: np.ndarray) -> np.ndarray:
    output = np.empty((len(values), model.n_components))
    covariances = model.covars_
    for state in range(model.n_components):
        output[:, state] = multivariate_normal.logpdf(
            values,
            mean=model.means_[state],
            cov=covariances[state],
            allow_singular=True,
        )
    return output


def causal_filter(
    model: GaussianHMM,
    train_values: np.ndarray,
    test_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    log_transition = np.log(np.clip(model.transmat_, 1e-300, None))
    train_emissions = _emission_log_likelihood(model, train_values)
    alpha = np.log(np.clip(model.startprob_, 1e-300, None)) + train_emissions[0]
    alpha -= logsumexp(alpha)
    for emission in train_emissions[1:]:
        alpha = logsumexp(alpha[:, None] + log_transition, axis=0) + emission
        alpha -= logsumexp(alpha)
    test_emissions = _emission_log_likelihood(model, test_values)
    probabilities = np.empty_like(test_emissions)
    states = np.empty(len(test_values), dtype=int)
    for index, emission in enumerate(test_emissions):
        alpha = logsumexp(alpha[:, None] + log_transition, axis=0) + emission
        alpha -= logsumexp(alpha)
        probabilities[index] = np.exp(alpha)
        states[index] = int(np.argmax(alpha))
    return states, probabilities


def _performance(returns: pd.Series, periods_per_year: int = 8760) -> dict[str, float]:
    clean = returns.fillna(0)
    equity = np.exp(clean.cumsum())
    peaks = np.maximum.accumulate(np.r_[1.0, equity.to_numpy()])[1:]
    drawdown = equity.to_numpy() / peaks - 1
    volatility = clean.std()
    return {
        "total_return": float(equity.iloc[-1] - 1),
        "annualized_return": float(np.exp(clean.mean() * periods_per_year) - 1),
        "annualized_volatility": float(volatility * np.sqrt(periods_per_year)),
        "sharpe": float(clean.mean() / volatility * np.sqrt(periods_per_year)) if volatility > 0 else np.nan,
        "max_drawdown": float(drawdown.min()),
    }


def _select_initial_states(frame: pd.DataFrame, config: dict[str, object]) -> tuple[int, int]:
    initial_end = pd.Timestamp(str(config["walk_forward_start"]), tz="UTC")
    initial = frame[frame.index < initial_end]
    split = int(len(initial) * 0.8)
    values = initial[REGIME_FEATURES].to_numpy()
    scaler = RobustScaler().fit(values[:split])
    train = scaler.transform(values[:split])
    validation = scaler.transform(values[split:])
    hmm_rows = []
    gmm_rows = []
    for states in config["candidate_states"]:
        model = fit_hmm(train, int(states), int(config["random_seed"]), 60)
        train_states = model.predict(train)
        if model.monitor_.converged and has_minimum_occupancy(train_states, int(states)):
            hmm_rows.append((float(model.score(validation) / len(validation)), int(states)))
        gmm = GaussianMixture(
            n_components=int(states),
            covariance_type="full",
            n_init=3,
            random_state=int(config["random_seed"]),
        ).fit(train)
        train_states = gmm.predict(train)
        if gmm.converged_ and has_minimum_occupancy(train_states, int(states)):
            gmm_rows.append((float(gmm.bic(validation)), int(states)))
    if not hmm_rows or not gmm_rows:
        raise ValueError("no initial HMM/GMM candidate passed convergence and 5% occupancy gates")
    return max(hmm_rows)[1], min(gmm_rows)[1]


def _fit_month(
    frame: pd.DataFrame,
    month_start: pd.Timestamp,
    end: pd.Timestamp,
    config: dict[str, object],
    hmm_state_count: int,
    gmm_state_count: int,
) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame] | None:
    month_end = min(month_start + pd.offsets.MonthBegin(1), end)
    train_start = month_start - pd.Timedelta(days=int(config["walk_forward_train_days"]))
    train = frame[(frame.index >= train_start) & (frame.index < month_start)]
    test = frame[(frame.index >= month_start) & (frame.index < month_end)]
    required_rows = int(config["walk_forward_train_days"]) * 24
    complete_span = (
        not train.empty
        and train.index.min() <= train_start + pd.Timedelta(hours=1)
        and train.index.max() >= month_start - pd.Timedelta(hours=1)
    )
    if not complete_span or len(train) < int(required_rows * 0.99) or test.empty:
        return None
    scaler = RobustScaler().fit(train[REGIME_FEATURES])
    train_scaled = scaler.transform(train[REGIME_FEATURES])
    test_scaled = scaler.transform(test[REGIME_FEATURES])
    seed = int(config["random_seed"])
    model = fit_hmm(train_scaled, hmm_state_count, seed, 60)
    if not model.monitor_.converged:
        raise ValueError(f"HMM failed to converge for {month_start.isoformat()}")
    train_states = model.predict(train_scaled)
    if not has_minimum_occupancy(train_states, hmm_state_count):
        raise ValueError(f"HMM occupancy gate failed for {month_start.isoformat()}")
    mapping = state_name_mapping(train_states, train, hmm_state_count)
    test_states, probabilities = causal_filter(model, train_scaled, test_scaled)
    alternate_hmm = fit_hmm(train_scaled, hmm_state_count, seed + 1, 60)
    if not alternate_hmm.monitor_.converged:
        raise ValueError(f"alternate HMM failed to converge for {month_start.isoformat()}")
    alternate_hmm_states = alternate_hmm.predict(train_scaled)
    if not has_minimum_occupancy(alternate_hmm_states, hmm_state_count):
        raise ValueError(f"alternate HMM occupancy gate failed for {month_start.isoformat()}")
    hmm_seed_ari = float(adjusted_rand_score(train_states, alternate_hmm_states))

    gmm = GaussianMixture(
        n_components=gmm_state_count,
        covariance_type="full",
        n_init=5,
        random_state=seed,
    ).fit(train_scaled)
    if not gmm.converged_:
        raise ValueError(f"GMM failed to converge for {month_start.isoformat()}")
    gmm_train_states = gmm.predict(train_scaled)
    if not has_minimum_occupancy(gmm_train_states, gmm_state_count):
        raise ValueError(f"GMM occupancy gate failed for {month_start.isoformat()}")
    gmm_mapping = state_name_mapping(gmm_train_states, train, gmm_state_count)
    gmm_probabilities = gmm.predict_proba(test_scaled)
    gmm_test_states = gmm_probabilities.argmax(axis=1)
    alternate_gmm = GaussianMixture(
        n_components=gmm_state_count,
        covariance_type="full",
        n_init=5,
        random_state=seed + 1,
    ).fit(train_scaled)
    if not alternate_gmm.converged_:
        raise ValueError(f"alternate GMM failed to converge for {month_start.isoformat()}")
    alternate_gmm_states = alternate_gmm.predict(train_scaled)
    if not has_minimum_occupancy(alternate_gmm_states, gmm_state_count):
        raise ValueError(f"alternate GMM occupancy gate failed for {month_start.isoformat()}")
    gmm_seed_ari = float(adjusted_rand_score(gmm_train_states, alternate_gmm_states))
    prior_month_start = month_start - pd.offsets.MonthBegin(1)
    prior_month = frame[(frame.index >= prior_month_start) & (frame.index < month_start)]
    prior_scaled = scaler.transform(prior_month[REGIME_FEATURES])
    retrospective = pd.DataFrame({
        "hmm_raw_state_current_refit": model.predict(prior_scaled),
        "gmm_raw_state_current_refit": gmm.predict(prior_scaled),
    }, index=prior_month.index)
    labels = pd.DataFrame({
        "time": test.index,
        "hmm_raw_state": test_states,
        "hmm_viterbi_raw_state": model.predict(test_scaled),
        "hmm_regime": [mapping[int(state)] for state in test_states],
        "hmm_max_probability": probabilities.max(axis=1),
        "gmm_raw_state": gmm_test_states,
        "gmm_regime": [gmm_mapping[int(state)] for state in gmm_test_states],
        "gmm_max_probability": gmm_probabilities.max(axis=1),
        "model_fit_end": train.index.max(),
        "train_start": train.index.min(),
        "feature_available_at": test.index,
        "prediction_for_end": test.index + pd.Timedelta(hours=1),
        "hmm_state_count": hmm_state_count,
        "gmm_state_count": gmm_state_count,
    }).set_index("time")

    gate_train = train.iloc[:-1]
    train_labels = pd.Series([mapping[int(state)] for state in train_states[:-1]], index=gate_train.index)
    trend_signal_train = np.sign(gate_train["sma_24"] - gate_train["sma_168"])
    reversion_signal_train = -np.sign(gate_train["zscore_24"])
    allowed: dict[str, set[str]] = {}
    for name, signal in (("trend", trend_signal_train), ("reversion", reversion_signal_train)):
        train_returns = signal * gate_train["forward_return_1h"]
        by_state = train_returns.groupby(train_labels).mean()
        allowed[name] = set(by_state[by_state > 0].index)
    gmm_train_labels = pd.Series([gmm_mapping[int(state)] for state in gmm_train_states[:-1]], index=gate_train.index)
    allowed_gmm: dict[str, set[str]] = {}
    for name, signal in (("trend", trend_signal_train), ("reversion", reversion_signal_train)):
        train_returns = signal * gate_train["forward_return_1h"]
        by_state = train_returns.groupby(gmm_train_labels).mean()
        allowed_gmm[name] = set(by_state[by_state > 0].index)
    labels["trend_allowed"] = labels["hmm_regime"].isin(allowed["trend"])
    labels["reversion_allowed"] = labels["hmm_regime"].isin(allowed["reversion"])
    labels["gmm_trend_allowed"] = labels["gmm_regime"].isin(allowed_gmm["trend"])
    labels["gmm_reversion_allowed"] = labels["gmm_regime"].isin(allowed_gmm["reversion"])
    monthly_row = {
        "month": month_start.isoformat(),
        "train_start": train.index.min().isoformat(),
        "model_fit_end": train.index.max().isoformat(),
        "train_rows": len(train),
        "gate_label_end": (gate_train.index.max() + pd.Timedelta(hours=1)).isoformat(),
        "test_rows": len(test),
        "converged": bool(model.monitor_.converged),
        "gmm_converged": bool(gmm.converged_),
        "hmm_train_min_occupancy": float(np.bincount(train_states, minlength=hmm_state_count).min() / len(train_states)),
        "gmm_train_min_occupancy": float(np.bincount(gmm_train_states, minlength=gmm_state_count).min() / len(gmm_train_states)),
        "hmm_mean_max_probability": float(labels["hmm_max_probability"].mean()),
        "gmm_mean_max_probability": float(labels["gmm_max_probability"].mean()),
        "hmm_seed_ari": hmm_seed_ari,
        "gmm_seed_ari": gmm_seed_ari,
        "trend_allowed_states": "|".join(sorted(allowed["trend"])) or "none",
        "reversion_allowed_states": "|".join(sorted(allowed["reversion"])) or "none",
        "gmm_trend_allowed_states": "|".join(sorted(allowed_gmm["trend"])) or "none",
        "gmm_reversion_allowed_states": "|".join(sorted(allowed_gmm["reversion"])) or "none",
    }
    return labels, monthly_row, retrospective


def run_walk_forward(
    hourly_features: pd.DataFrame,
    config: dict[str, object],
    root: Path,
    results_dir: Path,
) -> WalkForwardResult:
    tables_dir = results_dir / "tables"
    figures_dir = results_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables: list[str] = []
    figures: list[str] = []
    frame = hourly_features.dropna(subset=REGIME_FEATURES).copy()
    hmm_state_count, gmm_state_count = _select_initial_states(frame, config)
    walk_start = pd.Timestamp(str(config["walk_forward_start"]), tz="UTC")
    end = pd.Timestamp(str(config["end_date_exclusive"]), tz="UTC")
    month_starts = pd.date_range(walk_start, end, freq="MS", inclusive="left")
    label_frames = []
    monthly_rows = []

    max_workers = min(int(config.get("walk_forward_workers", 1)), len(month_starts))
    with threadpool_limits(limits=1), ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(
            lambda month_start: _fit_month(
                frame,
                month_start,
                end,
                config,
                hmm_state_count,
                gmm_state_count,
            ),
            month_starts,
        )
        for month_number, result in enumerate(results, start=1):
            if result is not None:
                labels, monthly_row, retrospective = result
                if label_frames:
                    previous = label_frames[-1]
                    common = previous.index.intersection(retrospective.index)
                    if len(common) >= int(config["min_cell_count"]):
                        monthly_row["hmm_retraining_ari"] = float(adjusted_rand_score(previous.loc[common, "hmm_viterbi_raw_state"], retrospective.loc[common, "hmm_raw_state_current_refit"]))
                        monthly_row["gmm_retraining_ari"] = float(adjusted_rand_score(previous.loc[common, "gmm_raw_state"], retrospective.loc[common, "gmm_raw_state_current_refit"]))
                        monthly_row["retraining_stability_status"] = "ok"
                    else:
                        monthly_row["hmm_retraining_ari"] = np.nan
                        monthly_row["gmm_retraining_ari"] = np.nan
                        monthly_row["retraining_stability_status"] = "insufficient_data"
                else:
                    monthly_row["hmm_retraining_ari"] = np.nan
                    monthly_row["gmm_retraining_ari"] = np.nan
                    monthly_row["retraining_stability_status"] = "not_applicable_first_window"
                label_frames.append(labels)
                monthly_rows.append(monthly_row)
            if month_number % 12 == 0:
                print(f"walk-forward: completed {month_number} monthly fits", flush=True)

    labels = pd.concat(label_frames).sort_index()
    labels_path = root / "data" / "research" / "walk_forward_labels.csv"
    write_csv_atomic(labels, labels_path, index=True)
    monthly = pd.DataFrame(monthly_rows)
    write_csv_atomic(monthly, tables_dir / "09_walk_forward_monthly.csv")
    tables.append("09_walk_forward_monthly.csv")
    provenance_columns = [
        "model_fit_end",
        "feature_available_at",
        "prediction_for_end",
        "train_start",
        "hmm_state_count",
        "gmm_state_count",
    ]
    provenance = labels[provenance_columns].reset_index()
    write_csv_atomic(provenance, tables_dir / "09_walk_forward_prediction_provenance.csv")
    tables.append("09_walk_forward_prediction_provenance.csv")
    audit_violations = int(
        (pd.to_datetime(provenance["model_fit_end"], utc=True) >= pd.to_datetime(provenance["feature_available_at"], utc=True)).sum()
        + (pd.to_datetime(provenance["feature_available_at"], utc=True) >= pd.to_datetime(provenance["prediction_for_end"], utc=True)).sum()
    )

    evaluated = frame.join(labels, how="inner")
    evaluated_for_performance = evaluated.dropna(subset=["forward_return_1h"]).copy()
    trend_signal = np.sign(evaluated_for_performance["sma_24"] - evaluated_for_performance["sma_168"])
    reversion_signal = -np.sign(evaluated_for_performance["zscore_24"])
    performance_rows = []
    equity_for_plot: dict[str, pd.Series] = {}
    for name, signal, gate in (
        ("trend", trend_signal, pd.Series(True, index=evaluated_for_performance.index)),
        ("trend_gated", trend_signal, evaluated_for_performance["trend_allowed"]),
        ("trend_gmm_gated", trend_signal, evaluated_for_performance["gmm_trend_allowed"]),
        ("reversion", reversion_signal, pd.Series(True, index=evaluated_for_performance.index)),
        ("reversion_gated", reversion_signal, evaluated_for_performance["reversion_allowed"]),
        ("reversion_gmm_gated", reversion_signal, evaluated_for_performance["gmm_reversion_allowed"]),
        ("buy_hold", pd.Series(1.0, index=evaluated_for_performance.index), pd.Series(True, index=evaluated_for_performance.index)),
    ):
        position = signal.where(gate, 0).fillna(0)
        for fee_bps in config["fee_bps"]:
            gross = position * evaluated_for_performance["forward_return_1h"]
            costs = position.diff().abs().fillna(position.abs()) * (float(fee_bps) / 10_000)
            net = gross - costs
            row = {"strategy": name, "fee_bps": int(fee_bps)} | _performance(net)
            performance_rows.append(row)
            if int(fee_bps) == 5:
                equity_for_plot[name] = np.exp(net.cumsum())
    performance = pd.DataFrame(performance_rows)
    write_csv_atomic(performance, tables_dir / "09_walk_forward_strategy_performance.csv")
    tables.append("09_walk_forward_strategy_performance.csv")

    state_rows = []
    for model_name in ("hmm", "gmm"):
        for regime, subset in evaluated.groupby(f"{model_name}_regime"):
            next_1h = subset["forward_return_1h"].dropna()
            next_24h = subset["forward_return_24h"].dropna()
            next_24h_vol = subset["forward_rv_24h"].dropna()
            next_24h_mae = subset["forward_mae_24h"].dropna()
            next_24h_mfe = subset["forward_mfe_24h"].dropna()
            ci_low, ci_high = _block_mean_ci(next_1h)
            return24_low, return24_high = _block_mean_ci(next_24h)
            vol_low, vol_high = _block_mean_ci(next_24h_vol)
            mae_low, mae_high = _block_mean_ci(next_24h_mae)
            mfe_low, mfe_high = _block_mean_ci(next_24h_mfe)
            state_rows.append({
                "model": model_name.upper(),
                "regime": regime,
                "count": len(next_1h),
                "label_count": len(subset),
                "occupancy": len(subset) / len(evaluated),
                "mean_probability": float(subset[f"{model_name}_max_probability"].mean()),
                "next_1h_return": float(next_1h.mean()),
                "next_1h_ci95_low": ci_low,
                "next_1h_ci95_high": ci_high,
                "next_24h_return": float(next_24h.mean()),
                "next_24h_return_ci95_low": return24_low,
                "next_24h_return_ci95_high": return24_high,
                "next_24h_vol": float(next_24h_vol.mean()),
                "next_24h_vol_ci95_low": vol_low,
                "next_24h_vol_ci95_high": vol_high,
                "next_24h_mae": float(next_24h_mae.mean()),
                "next_24h_mae_ci95_low": mae_low,
                "next_24h_mae_ci95_high": mae_high,
                "next_24h_mfe": float(next_24h_mfe.mean()),
                "next_24h_mfe_ci95_low": mfe_low,
                "next_24h_mfe_ci95_high": mfe_high,
                "status": "ok" if len(next_1h) >= int(config["min_cell_count"]) else "insufficient_data",
            })
    state_table = pd.DataFrame(state_rows)
    write_csv_atomic(state_table, tables_dir / "09_walk_forward_state_outcomes.csv")
    tables.append("09_walk_forward_state_outcomes.csv")

    transition_rows = []
    for model_name in ("hmm", "gmm"):
        transition_frame = pd.DataFrame({
            "current": evaluated[f"{model_name}_regime"],
            "next": evaluated[f"{model_name}_regime"].shift(-1),
            "next_time": pd.Series(evaluated.index, index=evaluated.index).shift(-1),
        })
        transition_frame = transition_frame[transition_frame["next_time"] - transition_frame.index == pd.Timedelta(hours=1)]
        counts = pd.crosstab(transition_frame["current"], transition_frame["next"])
        probabilities_table = counts.div(counts.sum(axis=1), axis=0)
        for from_state in counts.index:
            row_total = int(counts.loc[from_state].sum())
            for to_state in counts.columns:
                transition_count = int(counts.loc[from_state, to_state])
                indicator = (transition_frame.loc[transition_frame["current"] == from_state, "next"] == to_state).astype(float)
                probability_low, probability_high = _block_mean_ci(indicator)
                transition_rows.append({
                    "model": model_name.upper(),
                    "from_state": from_state,
                    "to_state": to_state,
                    "count": transition_count,
                    "probability": float(probabilities_table.loc[from_state, to_state]),
                    "probability_ci95_low": probability_low,
                    "probability_ci95_high": probability_high,
                    "status": "ok" if transition_count >= int(config["min_cell_count"]) else "insufficient_data",
                })
    write_csv_atomic(pd.DataFrame(transition_rows), tables_dir / "09_walk_forward_transition_matrix.csv")
    tables.append("09_walk_forward_transition_matrix.csv")

    fig, ax = plt.subplots(figsize=(14, 5))
    for name, equity in equity_for_plot.items():
        ax.plot(equity.index, equity, label=name)
    ax.set_yscale("log")
    ax.set_title("Walk-forward diagnostic strategies, 5 bps per position change")
    ax.legend(ncol=3)
    save_figure_atomic(fig, figures_dir / "09_walk_forward_performance.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    figures.append("09_walk_forward_performance.png")
    fee5 = performance[performance["fee_bps"] == 5].set_index("strategy")
    expected_prediction_times = pd.date_range(labels.index.min(), labels.index.max(), freq="h")
    unavailable_feature_times = expected_prediction_times.difference(frame.index)
    summary = {
        "hmm_state_count_locked_from_initial_window": hmm_state_count,
        "gmm_state_count_locked_from_initial_window": gmm_state_count,
        "prediction_rows": int(len(labels)),
        "first_prediction": labels.index.min().isoformat(),
        "last_prediction": labels.index.max().isoformat(),
        "future_reference_violations": audit_violations,
        "prediction_feature_unavailable_rows": int(len(unavailable_feature_times)),
        "hmm_mean_max_probability": float(labels["hmm_max_probability"].mean()),
        "gmm_mean_max_probability": float(labels["gmm_max_probability"].mean()),
        "hmm_mean_monthly_seed_ari": float(monthly["hmm_seed_ari"].mean()),
        "gmm_mean_monthly_seed_ari": float(monthly["gmm_seed_ari"].mean()),
        "hmm_mean_retraining_ari": float(monthly["hmm_retraining_ari"].mean()),
        "gmm_mean_retraining_ari": float(monthly["gmm_retraining_ari"].mean()),
        "fee5_performance": fee5[["total_return", "sharpe", "max_drawdown"]].to_dict(orient="index"),
    }
    return WalkForwardResult(summary, tables, figures)
