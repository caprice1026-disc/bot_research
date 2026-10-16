"""Causal, long-only time-series factor laboratory. No generated code execution."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

HOUR = 3_600_000
ANNUAL_HOURS = 365 * 24
FAMILIES = {
    "momentum": ("mom_6", "mom_24", "mom_72", "mom_168"),
    "reversal": ("ret_1", "distance_24", "distance_168"),
    "volatility": ("vol_24", "vol_168", "range_24"),
    "activity": ("volume_z_24", "volume_z_168"),
    "candle": ("close_location", "body", "gap"),
}
TERMINALS = tuple(t for family in FAMILIES.values() for t in family)
UNARY = ("neg", "abs", "tanh", "mean6", "mean24")
BINARY = ("add", "sub", "mul", "div")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def ms(text):
    return int(pd.Timestamp(text).timestamp() * 1000)


def iso(value):
    return pd.Timestamp(int(value), unit="ms", tz="UTC").isoformat()


@dataclass
class Panel:
    time: np.ndarray
    symbols: list[str]
    values: dict[str, np.ndarray]

    def prefix(self, count):
        return Panel(self.time[:count], self.symbols, {k: v[:count] for k, v in self.values.items()})


def save_panel(path, panel):
    np.savez_compressed(path, time=panel.time, symbols=np.asarray(panel.symbols), **panel.values)


def load_panel(path):
    with np.load(path, allow_pickle=False) as data:
        return Panel(data["time"], data["symbols"].tolist(), {k: data[k] for k in ("open", "high", "low", "close", "volume")})


def join_panels(a, b):
    if a.symbols != b.symbols or b.time[0] != a.time[-1] + HOUR:
        raise ValueError("Non-contiguous panel join")
    return Panel(np.concatenate([a.time, b.time]), a.symbols,
                 {k: np.concatenate([a.values[k], b.values[k]]) for k in a.values})


def aggregate_sources(source, cfg):
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest["venue"] != "binance_spot_public" or manifest["interval_ms"] != 300_000:
        raise ValueError("Expected Binance spot five-minute manifest")
    times = np.arange(ms(cfg["data_start"]), ms(cfg["data_end"]), 300_000, dtype=np.int64)
    arrays, evidence = {}, {"source_manifest_sha256": file_hash(source / "manifest.json"), "datasets": {}}
    for symbol in cfg["symbols"]:
        entry = manifest["datasets"][symbol]
        path = source / entry["path"]
        if not path.resolve().is_relative_to(source.resolve()) or file_hash(path) != entry["sha256"]:
            raise ValueError("Source hash or path mismatch")
        frame = pd.read_csv(path)
        if len(frame) != len(times) or not np.array_equal(frame.open_ms.values, times):
            raise ValueError("Missing, duplicate, misaligned or out-of-order five-minute rows")
        if not np.array_equal(frame.available_ms.values, times + 300_000):
            raise ValueError("Incorrect source availability")
        price = frame[["open", "high", "low", "close"]].values
        if not np.isfinite(frame.values).all() or (price <= 0).any() or (frame.volume.values < 0).any():
            raise ValueError("Non-finite or invalid OHLCV")
        if (price[:, 1] < price.max(axis=1)).any() or (price[:, 2] > price.min(axis=1)).any():
            raise ValueError("OHLC inconsistency")
        arrays[symbol] = {"open": frame.open.values.reshape(-1, 12)[:, 0],
                          "high": frame.high.values.reshape(-1, 12).max(axis=1),
                          "low": frame.low.values.reshape(-1, 12).min(axis=1),
                          "close": frame.close.values.reshape(-1, 12)[:, -1],
                          "volume": frame.volume.values.reshape(-1, 12).sum(axis=1)}
        evidence["datasets"][symbol] = {"sha256": entry["sha256"], "five_minute_rows": len(frame),
                                         "hourly_rows": len(frame) // 12}
    panel = Panel(times[::12], cfg["symbols"],
                  {k: np.column_stack([arrays[s][k] for s in cfg["symbols"]]) for k in arrays[cfg["symbols"][0]]})
    return panel, evidence


def rolling(a, window, statistic="mean"):
    obj = pd.DataFrame(a).rolling(window, min_periods=window)
    return getattr(obj, statistic)().values


def lag(a, hours=1):
    out = np.full_like(a, np.nan, dtype=float)
    out[hours:] = a[:-hours]
    return out


def make_features(panel):
    v = panel.values
    c, o, h, lo, volume = (v[k] for k in ("close", "open", "high", "low", "volume"))
    r = c / lag(c) - 1
    features = {"ret_1": r}
    for window in (6, 24, 72, 168):
        features[f"mom_{window}"] = c / lag(c, window) - 1
    for window in (24, 168):
        features[f"distance_{window}"] = c / rolling(c, window) - 1
        features[f"vol_{window}"] = rolling(r, window, "std")
        features[f"volume_z_{window}"] = (volume - rolling(volume, window)) / np.maximum(rolling(volume, window, "std"), 1e-8)
    features["range_24"] = rolling((h - lo) / c, 24)
    features["close_location"] = 2 * (c - lo) / np.maximum(h - lo, 1e-8) - 1
    features["body"] = (c - o) / o
    features["gap"] = o / lag(c) - 1
    return features


def validate_ast(tree, max_depth=4, max_nodes=15):
    def check(node, depth):
        if not isinstance(node, dict) or depth > max_depth:
            raise ValueError("Invalid AST object/depth")
        if set(node) == {"terminal"} and node["terminal"] in TERMINALS:
            return 1
        if set(node) != {"op", "args"} or not isinstance(node["op"], str) or not isinstance(node["args"], list):
            raise ValueError("AST only accepts allowlisted scalar-score operators")
        op = node["op"]
        arity = 1 if op in UNARY else 2 if op in BINARY else -1
        if len(node["args"]) != arity:
            raise ValueError("Unknown operator or arity")
        return 1 + sum(check(x, depth + 1) for x in node["args"])
    nodes = check(tree, 1)
    if nodes > max_nodes:
        raise ValueError("AST node limit")
    return nodes


class Evaluator:
    def __init__(self, features, cfg):
        self.features = features
        self.cfg = cfg
        self._cached = lru_cache(maxsize=96)(self._evaluate)

    def __call__(self, tree):
        validate_ast(tree, self.cfg["max_depth"], self.cfg["max_nodes"])
        return self._cached(canonical(tree))

    def _evaluate(self, key):
        tree = json.loads(key)
        if "terminal" in tree:
            return self.features[tree["terminal"]]
        op = tree["op"]
        a = self._cached(canonical(tree["args"][0]))
        if op in BINARY:
            b = self._cached(canonical(tree["args"][1]))
            with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                result = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
                          "div": lambda: a / np.where(np.abs(b) < 1e-3, np.where(b < 0, -1e-3, 1e-3), b)}[op]()
        else:
            result = {"neg": lambda: -a, "abs": lambda: np.abs(a), "tanh": lambda: np.tanh(a),
                      "mean6": lambda: rolling(a, 6), "mean24": lambda: rolling(a, 24)}[op]()
        return np.clip(result, -1e6, 1e6)


def fit_normalizer(raw, train_mask):
    train = raw[train_mask]
    if len(train) < 100 or not np.isfinite(train).all():
        raise ValueError("Insufficient finite train-only normalizer samples")
    center, scale = train.mean(axis=0), train.std(axis=0)
    return {"mean": center.tolist(), "std": np.maximum(scale, 1e-8).tolist(), "rows": len(train)}


def normalize(raw, params):
    score = (raw - np.asarray(params["mean"])) / np.asarray(params["std"])
    return np.clip(np.nan_to_num(score, nan=0.0, posinf=5.0, neginf=-5.0), -5, 5)


def make_weights(score, features, cfg, delay=None):
    vol = np.maximum(features["vol_168"] * np.sqrt(ANNUAL_HOURS), 1e-6)
    scale = np.minimum(1, cfg["annual_asset_vol_target"] / vol)
    raw = cfg["max_asset_weight"] * np.maximum(0, np.tanh(score)) * scale
    raw = np.nan_to_num(raw, nan=0.0)
    weights = rolling(raw, cfg["signal_smoothing_hours"])
    weights = np.nan_to_num(weights, nan=0.0)
    # Bar k closes at open(k+1). Additional latency d executes at open(k+1+d).
    return np.nan_to_num(lag(weights, 1 + (cfg["execution_delay_hours"] if delay is None else delay)), nan=0.0)


def fold_indices(panel, start, end, cfg):
    start_ms = ms(start) + cfg["embargo_hours"] * HOUR
    end_ms = ms(end) - cfg["purge_hours"] * HOUR
    indices = np.flatnonzero((panel.time >= start_ms) & (panel.time < end_ms))
    if not len(indices) or indices[-1] + 1 >= len(panel.time):
        raise ValueError("Fold has no complete execution intervals")
    if not np.all(np.diff(indices) == 1):
        raise ValueError("Non-contiguous fold")
    return indices


def train_mask(panel, cfg):
    return (panel.time >= ms(cfg["train_start"])) & (panel.time < ms(cfg["train_end"]) - cfg["purge_hours"] * HOUR)


def simulate(weights, returns, fee_bps, initial=10000.0):
    """Exact proportional-fee, self-financing, fractional-spot rebalancing.

    w is the target fraction AFTER paying fees. Solve x = 1-c*|x*w-drift|.
    Drift weights depend only on previous post-fee targets and observed returns,
    so the fee fixed points can be solved for every timestamp in parallel.
    """
    w, r = np.asarray(weights, float), np.asarray(returns, float)
    if w.shape != r.shape or not np.isfinite(w).all() or not np.isfinite(r).all():
        raise ValueError("Non-finite or mismatched account arrays")
    if (w < 0).any() or (w.sum(axis=1) > 1 + 1e-12).any() or (r <= -1).any():
        raise ValueError("Long-only cash account violation")
    gross_step = 1 + np.sum(w * r, axis=1)
    drift = np.zeros_like(w)
    drift[1:] = w[:-1] * (1 + r[:-1]) / gross_step[:-1, None]
    fee_rate = fee_bps / 10000
    x = np.ones(len(w))
    for _ in range(10):
        x = 1 - fee_rate * np.abs(x[:, None] * w - drift).sum(axis=1)
    relative_trades = x[:, None] * w - drift
    fees_fraction = fee_rate * np.abs(relative_trades).sum(axis=1)
    if not np.allclose(x, 1 - fees_fraction, atol=1e-13):
        raise ArithmeticError("Fee fixed point failed")
    step = x * gross_step
    before = initial * np.concatenate(([1.0], np.cumprod(step[:-1])))
    after = before * step
    notionals = before[:, None] * relative_trades
    fees = np.abs(notionals) * fee_rate
    liquidation_notional = after[-1] * (w[-1] * (1 + r[-1]) / gross_step[-1])
    liquidation_fees = liquidation_notional * fee_rate
    final = after[-1] - liquidation_fees.sum()
    pnl = before * x * np.sum(w * r, axis=1)
    if not np.isclose(initial + pnl.sum() - fees.sum() - liquidation_fees.sum(), final, atol=1e-7):
        raise ArithmeticError("Cash/share conservation failed")
    net_r = step - 1
    net_r[-1] = final / before[-1] - 1
    equity = after.copy()
    equity[-1] = final
    path = np.concatenate(([initial], equity))
    dd = path / np.maximum.accumulate(path) - 1
    sd = net_r.std(ddof=1) if len(net_r) > 1 else 0.0
    total_turnover = np.abs(relative_trades).sum() + liquidation_notional.sum() / after[-1]
    trades = int((np.abs(relative_trades) > 1e-10).sum() + (liquidation_notional > initial * 1e-10).sum())
    annual_log = float(np.log(final / initial) * ANNUAL_HOURS / len(w))
    metrics = {"net_return": float(final / initial - 1), "net_profit": float(final - initial),
               "max_drawdown": float(dd.min()), "sharpe": float(net_r.mean() / sd * np.sqrt(ANNUAL_HOURS)) if sd > 1e-12 else 0.0,
               "annualized_return": float(np.expm1(annual_log)) if annual_log < 700 else None,
               "turnover_one_way": float(total_turnover), "turnover_per_day": float(total_turnover / (len(w) / 24)),
               "trades": trades, "cost": float(fees.sum() + liquidation_fees.sum()),
               "cost_over_initial": float((fees.sum() + liquidation_fees.sum()) / initial),
               "initial_equity": initial, "final_equity": float(final), "hours": len(w),
               "mean_gross_exposure": float(w.sum(axis=1).mean()),
               "accounting_residual": float(final - initial - pnl.sum() + fees.sum() + liquidation_fees.sum())}
    return {"metrics": metrics, "equity": equity, "before": before, "after_fee": before * x,
            "notionals": notionals, "fees": fees, "liquidation_notional": liquidation_notional,
            "liquidation_fees": liquidation_fees, "pnl": pnl, "returns": net_r, "weights": w}


def forward_labels(panel, horizon, delay):
    o = panel.values["open"]
    label = np.full_like(o, np.nan)
    offset = 1 + delay
    label[:-(offset + horizon)] = o[offset + horizon:] / o[offset:-horizon] - 1
    return label


def temporal_ic(score, labels, mask):
    out = {}
    for col in range(score.shape[1]):
        valid = mask & np.isfinite(labels[:, col]) & np.isfinite(score[:, col])
        x, y = score[valid, col], labels[valid, col]
        out[str(col)] = float(np.corrcoef(x, y)[0, 1]) if len(x) > 10 and x.std() > 1e-12 and y.std() > 1e-12 else None
    return out


def tree_family(tree):
    counts = Counter()
    def walk(node):
        if "terminal" in node:
            counts.update([next(k for k, v in FAMILIES.items() if node["terminal"] in v)])
        else:
            for arg in node["args"]:
                walk(arg)
    walk(tree)
    return sorted(counts, key=lambda x: (-counts[x], x))[0]


def random_tree(rng, cfg, priorities, depth=1):
    if depth >= cfg["max_depth"] or rng.random() < 0.40:
        families = list(FAMILIES)
        probability = np.asarray([priorities[x] for x in families], float)
        family = families[int(rng.choice(len(families), p=probability / probability.sum()))]
        return {"terminal": str(rng.choice(FAMILIES[family]))}
    op = str(rng.choice(UNARY + BINARY))
    return {"op": op, "args": [random_tree(rng, cfg, priorities, depth + 1) for _ in range(1 if op in UNARY else 2)]}


def all_paths(tree):
    paths = [()]
    for i, arg in enumerate(tree.get("args", [])):
        paths.extend((i,) + p for p in all_paths(arg))
    return paths


def at_path(tree, path):
    for part in path:
        tree = tree["args"][part]
    return tree


def replace_path(tree, path, replacement):
    if not path:
        return replacement
    copied = json.loads(canonical(tree))
    at_path(copied, path[:-1])["args"][path[-1]] = replacement
    return copied


def offspring(rng, population, cfg, priorities):
    def parent():
        choices = rng.choice(len(population), size=min(3, len(population)), replace=False)
        return max((population[int(i)] for i in choices), key=lambda x: x["fitness"])["tree"]
    if not population or rng.random() < 0.25:
        return random_tree(rng, cfg, priorities)
    a = parent()
    paths = all_paths(a)
    path = paths[int(rng.integers(len(paths)))]
    if rng.random() < 0.5:
        b = parent()
        b_paths = all_paths(b)
        replacement = at_path(b, b_paths[int(rng.integers(len(b_paths)))])
    else:
        replacement = random_tree(rng, cfg, priorities, len(path) + 1)
    return replace_path(a, path, replacement)


@dataclass(frozen=True)
class PolicyPatch:
    priorities: dict[str, float]
    rationale: str


class Proposer(Protocol):
    def propose(self, development_evidence: dict, current_priorities: dict[str, float]) -> PolicyPatch: ...


class OfflineProposer:
    """Deterministic research-distribution proposal; never changes metric/gates."""
    def propose(self, development_evidence, current_priorities):
        means = development_evidence["family_mean_fitness"]
        ranked = sorted(FAMILIES, key=lambda x: (-means.get(x, -1e9), x))
        priorities = {family: float(np.clip(1.5 - 0.25 * ranked.index(family), 0.5, 2.0)) for family in FAMILIES}
        return PolicyPatch(priorities, "Prefer development-supported families, retaining every family at >=0.5 weight; apply next cycle only")


class LLMProposer:
    """Future adapter seam. Accept structured evidence, return bounded JSON patch.

    Deliberately has no SDK, credential lookup, network or code execution path.
    An explicit future implementation must validate against validate_patch().
    """
    def propose(self, development_evidence, current_priorities):
        raise RuntimeError("LLM calls disabled: this experiment is an offline adaptation")


def validate_patch(patch):
    if set(patch.priorities) != set(FAMILIES) or not all(0.5 <= v <= 2.0 for v in patch.priorities.values()):
        raise ValueError("Patch can only adjust existing family priorities in [0.5, 2]")
    return patch


def block_permutation(returns, rng, block):
    blocks = [returns[i:i + block] for i in range(0, len(returns), block)]
    return np.concatenate([blocks[int(i)] for i in rng.permutation(len(blocks))])


def search(development, cfg, method, seed, destination):
    # The only data argument is a panel ending before test. No holdout path accepted.
    if development.time[-1] >= ms(cfg["test_start"]):
        raise ValueError("Search cannot accept holdout observations")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    null_rng = np.random.default_rng(seed + 9_000_000)
    features = make_features(development)
    evaluator = Evaluator(features, cfg)
    fit_mask = train_mask(development, cfg)
    boundaries = cfg["validation_boundaries"]
    folds = [fold_indices(development, a, b, cfg) for a, b in zip(boundaries[:-1], boundaries[1:])]
    open_returns = development.values["open"][1:] / development.values["open"][:-1] - 1
    fold_returns = [open_returns[idx] for idx in folds]
    if method == "noise_gp":
        fold_returns = [block_permutation(r, null_rng, cfg["null_block_hours"]) for r in fold_returns]
    labels = forward_labels(development, cfg["label_horizon_hours"], cfg["execution_delay_hours"])
    priorities = {x: 1.0 for x in FAMILIES}
    population, seen, candidates, policies = [], set(), [], []
    score_samples = {}
    attempts_total = 0
    trial_file = (destination / "trials.jsonl").open("w", encoding="utf-8")
    attempt_file = (destination / "attempts.jsonl").open("w", encoding="utf-8")
    for cycle in range(cfg["cycles"]):
        policy = {"cycle": cycle, "priorities": dict(priorities), "metric_hash": digest({k: cfg[k] for k in
                  ("fee_bps", "execution_delay_hours", "signal_smoothing_hours", "complexity_penalty")})}
        policy["hash"] = digest(policy)
        policies.append(policy)
        for local_trial in range(cfg["evaluations_per_cycle"]):
            trial = len(candidates)
            for attempt in range(cfg["max_generation_attempts_per_trial"]):
                attempts_total += 1
                # Shared economic starter set costs one evaluation each; no free trials.
                if trial < 2 * len(TERMINALS) and attempt == 0:
                    terminal = {"terminal": TERMINALS[trial // 2]}
                    tree = terminal if trial % 2 == 0 else {"op": "neg", "args": [terminal]}
                else:
                    tree = random_tree(rng, cfg, priorities) if method == "random" else offspring(rng, population, cfg, priorities)
                key = canonical(tree)
                reason = None
                try:
                    nodes = validate_ast(tree, cfg["max_depth"], cfg["max_nodes"])
                except ValueError:
                    reason = "structural_limit"
                if key in seen:
                    reason = "syntactic_duplicate"
                attempt_file.write(canonical({"trial": trial, "cycle": cycle, "attempt": attempt, "tree": tree,
                                               "status": reason or "evaluate"}) + "\n")
                if reason is None:
                    seen.add(key)
                    break
            else:
                raise RuntimeError("Bounded generation attempts exhausted before equal evaluation budget")
            raw = evaluator(tree)
            params = fit_normalizer(raw, fit_mask)
            score = normalize(raw, params)
            weights = make_weights(score, features, cfg)
            stats = [simulate(weights[idx], r, cfg["fee_bps"], cfg["initial_equity"])["metrics"]
                     for idx, r in zip(folds, fold_returns)]
            fitness = min(s["sharpe"] for s in stats) - cfg["complexity_penalty"] * nodes
            family = tree_family(tree)
            sample = score[folds[0][::8]].ravel()
            corr_reject = False
            max_corr = 0.0
            if method != "random" and sample.std() > 1e-10:
                for item in population:
                    previous = score_samples[item["id"]]
                    if previous.std() > 1e-10:
                        corr = abs(float(np.corrcoef(sample, previous)[0, 1]))
                        max_corr = max(max_corr, corr)
                corr_reject = max_corr > cfg["correlation_limit"]
            candidate = {"id": digest(tree)[:16], "tree": tree, "family": family, "nodes": nodes,
                         "trial": trial, "cycle": cycle, "fitness": float(fitness), "normalizer": params,
                         "fold_metrics": stats, "correlation_rejected": corr_reject, "max_archive_correlation": max_corr,
                         "policy_hash": policy["hash"]}
            candidates.append(candidate)
            score_samples[candidate["id"]] = sample
            if not corr_reject:
                population.append(candidate)
                # Family-stratified elites: first preserve one per family, then fill by fitness (max 6 per family).
                ranked = sorted(population, key=lambda x: (-x["fitness"], x["id"]))
                representatives, used = [], set()
                for item in ranked:
                    if item["family"] not in used:
                        representatives.append(item)
                        used.add(item["family"])
                counts = Counter(x["family"] for x in representatives)
                for item in ranked:
                    if item not in representatives and counts[item["family"]] < 6 and len(representatives) < cfg["population_size"]:
                        representatives.append(item)
                        counts[item["family"]] += 1
                population = representatives
            trial_file.write(canonical(candidate) + "\n")
            if (trial + 1) % 128 == 0:
                print(f"{method} seed={seed} evaluated={trial+1} best={max(x['fitness'] for x in candidates):.3f}", flush=True)
        cycle_candidates = [x for x in candidates if x["cycle"] == cycle]
        evidence = {"completed_cycle": cycle, "data_max_time": iso(development.time[-1]),
                    "family_mean_fitness": {f: float(np.mean([x["fitness"] for x in cycle_candidates if x["family"] == f]))
                                             for f in FAMILIES if any(x["family"] == f for x in cycle_candidates)},
                    "family_counts": dict(Counter(x["family"] for x in cycle_candidates)),
                    "best_fitness": max(x["fitness"] for x in candidates), "test_observations": 0}
        if method in ("dual_gp", "noise_gp") and cycle + 1 < cfg["cycles"]:
            patch = validate_patch(OfflineProposer().propose(evidence, priorities))
            write_json(destination / f"proposal_cycle_{cycle}.json", {"evidence": evidence, "proposer": "deterministic_offline",
                       "next_cycle_only": True, "patch": {"priorities": patch.priorities, "rationale": patch.rationale}})
            priorities = patch.priorities
    trial_file.close()
    attempt_file.close()
    # Correlation gates control breeding/diversity, while the predeclared dev winner is selected from every evaluated expression.
    winner = max(candidates, key=lambda x: (x["fitness"], -x["nodes"], x["id"]))
    admissible = all(x["sharpe"] > cfg["admission_min_fold_sharpe"] and x["trades"] >= cfg["admission_min_fold_trades"]
                     and x["max_drawdown"] >= cfg["admission_max_fold_drawdown"] for x in winner["fold_metrics"])
    winner = dict(winner, admitted=admissible)
    raw = evaluator(winner["tree"])
    score = normalize(raw, winner["normalizer"])
    val_mask = np.zeros(len(development.time), bool)
    for idx in folds:
        val_mask[idx] = True
    # IC labels correspond to the score bar and cannot reach a fold end.
    label_mask = np.zeros_like(val_mask)
    for a, b in zip(boundaries[:-1], boundaries[1:]):
        label_mask |= (development.time >= ms(a) + cfg["embargo_hours"] * HOUR) & (
            development.time + (1 + cfg["execution_delay_hours"] + cfg["label_horizon_hours"]) * HOUR < ms(b))
    winner["validation_temporal_ic_24h"] = temporal_ic(score, labels, label_mask)
    result = {"method": method, "seed": seed, "winner": winner, "evaluations": len(candidates),
              "generation_attempts": attempts_total, "policies": policies, "test_observations_seen": 0,
              "data_end_exclusive": iso(development.time[-1] + HOUR), "llm_calls": 0,
              "objective": "minimum costed fold Sharpe minus 0.01 per AST node",
              "null_search": method == "noise_gp", "null_permutation_hash": digest([r.tolist() for r in fold_returns]) if method == "noise_gp" else None}
    write_json(destination / "selection.json", result)
    return result
