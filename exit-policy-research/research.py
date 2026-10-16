"""Offline price-only exit timing experiment. No network or order interface."""
from __future__ import annotations

import argparse
import copy
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

# Set before importing numerical runtimes; also explicitly restrict torch below.
for _key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import numpy as np
import pandas as pd
import psutil
import torch
from torch import nn
from torch.nn import functional as F

BAR_MS = 300000
FEATURES = ["ret_1h", "ret_6h", "ret_24h", "ret_7d", "vol_24h",
            "range_1h", "volume_ratio_24h", "tod_sin", "tod_cos",
            "age_fraction", "unrealized_return", "mfe", "mae", "draw_from_peak"]
_JOB = None


def memory_guard(limit_mb):
    """Hard per-process committed-memory cap on Windows, not just a hint."""
    global _JOB
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit_mb * 2**20, limit_mb * 2**20))
        return "RLIMIT_AS"
    from ctypes import wintypes as w
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = w.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.GetCurrentProcess.restype = w.HANDLE
    _JOB = k.CreateJobObjectW(None, None)
    lim = EXT()
    lim.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    lim.ProcessMemoryLimit = int(limit_mb) * 2**20
    if not _JOB or not k.SetInformationJobObject(_JOB, 9, ctypes.byref(lim), ctypes.sizeof(lim)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k.AssignProcessToJobObject(_JOB, k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return "Windows Job Object ProcessMemoryLimit"


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def utc(ms):
    return pd.to_datetime(int(ms), unit="ms", utc=True).isoformat()


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_data(path):
    d = pd.read_csv(path)
    required = ["open_ms", "available_ms", "open", "high", "low", "close", "volume"]
    if list(d.columns) != required:
        raise ValueError(f"Unexpected schema: {path}")
    if not np.isfinite(d.to_numpy()).all() or not (np.diff(d.open_ms) == BAR_MS).all():
        raise ValueError("Nonfinite, duplicate, unsorted or missing bars")
    if not ((d.available_ms - d.open_ms == BAR_MS).all() and (d.open_ms % BAR_MS == 0).all()):
        raise ValueError("Unexpected availability timestamp")
    if not ((d[["open", "high", "low", "close"]] > 0).all().all() and (d.volume >= 0).all()):
        raise ValueError("Nonpositive price or negative volume")
    if not ((d.high >= d[["open", "close", "low"]].max(axis=1)).all() and (d.low <= d[["open", "close", "high"]].min(axis=1)).all()):
        raise ValueError("Invalid OHLC")
    return d


def market_features(d):
    """Each row is known only at its available_ms (bar close)."""
    c = d.close
    r = np.log(c).diff()
    hour = (d.available_ms // 3600000) % 24
    return np.column_stack([c.pct_change(k) for k in (12, 72, 288, 2016)] + [
        r.rolling(288).std(), d.high.rolling(12).max() / d.low.rolling(12).min() - 1,
        d.volume.rolling(12).sum() / d.volume.rolling(288).mean().clip(lower=1e-12) / 12,
        np.sin(hour * 2 * np.pi / 24), np.cos(hour * 2 * np.pi / 24)]).astype("float32")


def protective_exit(o, h, l, stop, target):
    # Worst feasible event order for long-only: stop before target if both touched.
    if o <= stop:
        return float(o), "gap_stop"
    if o >= target:
        return float(target), "gap_tp_capped"
    if l <= stop:
        return float(stop), "stop"
    if h >= target:
        return float(target), "take_profit"
    return None


def cash_result(cash, entry_raw, exit_raw, cfg, cost_mult=1):
    fee = cfg["fee_bps"] * 1e-4 * cost_mult
    slip = cfg["slippage_bps"] * 1e-4 * cost_mult
    entry_fill = entry_raw * (1 + slip)
    exit_fill = exit_raw * (1 - slip)
    qty = cash / (entry_fill * (1 + fee))
    proceeds = qty * exit_fill * (1 - fee)
    fees = qty * (entry_fill + exit_fill) * fee
    slippage = qty * (entry_fill - entry_raw + exit_raw - exit_fill)
    return {"qty": qty, "entry_fill": entry_fill, "exit_fill": exit_fill,
            "end_cash": proceeds, "net_pnl": proceeds - cash,
            "gross_pnl": qty * (exit_raw - entry_raw), "fees": fees,
            "slippage": slippage, "cost": fees + slippage,
            "turnover": qty * (entry_fill + exit_fill)}


def make_episode(d, mf, start, cfg, asset, cost_mult=1, extra_delay=0):
    horizon = cfg["max_hold_hours"] * 12
    stop = float(d.open.iloc[start]) * (1 + cfg["slippage_bps"] * 1e-4 * cost_mult) * (1 - cfg["stop_loss"])
    target = float(d.open.iloc[start]) * (1 + cfg["slippage_bps"] * 1e-4 * cost_mult) * (1 + cfg["take_profit"])
    raw_entry = float(d.open.iloc[start])
    terminal_open = float(d.open.iloc[start + horizon])
    # Resting protection remains active at the max-hold open. Only its open is
    # known when closing; the terminal bar's later high/low must not be inspected.
    if terminal_open <= stop:
        terminal_raw, terminal_reason = terminal_open, "gap_stop"
    elif terminal_open >= target:
        terminal_raw, terminal_reason = target, "gap_tp_capped"
    else:
        terminal_raw, terminal_reason = terminal_open, "max_hold"
    forced_j, forced_raw, forced_reason = horizon, terminal_raw, terminal_reason
    for j in range(horizon):
        row = d.iloc[start + j]
        p = protective_exit(row.open, row.high, row.low, stop, target)
        if p:
            forced_j, (forced_raw, forced_reason) = j, p
            break
    offsets, x, valid = [], [], []
    results = []
    closes = d.close.to_numpy()
    fill = raw_entry * (1 + cfg["slippage_bps"] * 1e-4 * cost_mult)
    for age in range(cfg["decision_hours"] * 12, horizon, cfg["decision_hours"] * 12):
        # Decision after the preceding completed bar; protection has priority on that bar.
        if age - 1 >= forced_j:
            break
        idx = start + age - 1
        hist = closes[start:idx + 1]
        state = np.r_[mf[idx], age / horizon, closes[idx] / fill - 1,
                      hist.max() / fill - 1, hist.min() / fill - 1, closes[idx] / hist.max() - 1]
        if not np.isfinite(state).all():
            raise ValueError("Feature warmup insufficient")
        offsets.append(age)
        x.append(state)
        valid.append(age >= cfg["min_hold_hours"] * 12)
        execution = age + extra_delay  # normal next bar open, plus optional 5m delay
        if forced_j < execution:
            results.append((forced_j, forced_raw, forced_reason))
        else:
            # A queued sell at open executes before intrabar stop/TP; gap protection takes priority.
            px = float(d.open.iloc[start + execution])
            if px <= stop:
                results.append((execution, px, "gap_stop"))
            elif px >= target:
                results.append((execution, target, "gap_tp_capped"))
            else:
                results.append((execution, px, "policy"))
    return {"id": f"{asset}:{int(d.open_ms.iloc[start])}", "asset": asset, "start": int(start),
            "end": int(start + horizon), "entry_ms": int(d.open_ms.iloc[start]),
            "end_ms": int(d.open_ms.iloc[start + horizon]), "entry_raw": raw_entry,
            "x": np.asarray(x, dtype="float32").reshape(-1, len(FEATURES)),
            "offsets": np.asarray(offsets), "valid": np.asarray(valid, dtype=bool),
            "candidates": results, "forced": (forced_j, forced_raw, forced_reason)}


def build_splits(data, cfg):
    first = max(int(d.open_ms.iloc[0]) for d in data.values()) + cfg["warmup_days"] * 86400000
    last = min(int(d.open_ms.iloc[-1]) + BAR_MS for d in data.values())
    span = last - first
    edges = [first, first + int(span * cfg["split_ratios"][0] // BAR_MS) * BAR_MS,
             first + int(span * sum(cfg["split_ratios"][:2]) // BAR_MS) * BAR_MS, last]
    splits = {s: [] for s in ("train", "val", "test")}
    purged = []
    mf = {a: market_features(d) for a, d in data.items()}
    stride = cfg["entry_stride_hours"] * 3600000
    for t in range(first, last, stride):
        for a, d in data.items():
            start = int(np.searchsorted(d.open_ms.to_numpy(), t))
            end = start + cfg["max_hold_hours"] * 12
            split = next((s for i, s in enumerate(splits) if edges[i] <= t and end < len(d) and int(d.open_ms.iloc[end]) < edges[i + 1]), None)
            if split is None:
                purged.append({"asset": a, "entry_ms": t, "reason": "full_path_crosses_split_or_end"})
                continue
            ep = make_episode(d, mf[a], start, cfg, a)
            splits[split].append(ep)
    for s, eps in splits.items():
        if len(eps) < 10:
            raise ValueError(f"Insufficient episodes: {s}={len(eps)}")
    return splits, edges, purged, mf


def fit_scaler(eps):
    x = np.concatenate([e["x"] for e in eps if len(e["x"])])
    return {"mean": x.mean(axis=0).astype("float64"), "std": np.maximum(x.std(axis=0), 1e-6).astype("float64")}


def transform(x, scaler):
    return np.clip((x - scaler["mean"]) / scaler["std"], -20, 20).astype("float32")


def oracle_labels(ep, cfg, split="train"):
    if split != "train":
        raise ValueError("Oracle label construction is restricted to train episodes")
    eligible = np.flatnonzero(ep["valid"])
    exits = [ep["candidates"][j] for j in eligible] + [ep["forced"]]
    scores = [cash_result(1., ep["entry_raw"], z[1], cfg)["net_pnl"] for z in exits]
    # Candidates in chronological order; np.argmax picks earliest equal maximum.
    chosen = int(np.argmax(scores))
    best_j = int(eligible[chosen]) if chosen < len(eligible) else len(ep["x"])
    y = (np.arange(len(ep["x"])) >= best_j).astype("float32")
    return y, best_j


class ActorCritic(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        h1, h2 = cfg["hidden"]
        self.trunk = nn.Sequential(nn.Linear(len(FEATURES), h1), nn.ReLU(), nn.Linear(h1, h2), nn.ReLU())
        self.actor = nn.Linear(h2, 1)
        self.value = nn.Linear(h2, 1)

    def forward(self, x):
        z = self.trunk(x)
        return self.actor(z).squeeze(-1), self.value(z).squeeze(-1)


def train_mlp(eps, scaler, cfg, seed):
    seed_all(seed)
    model = ActorCritic(cfg)
    xs, ys = [], []
    audit = []
    for e in eps:
        y, best = oracle_labels(e, cfg)
        xs.append(transform(e["x"][e["valid"]], scaler))
        ys.append(y[e["valid"]])
        audit.append({"episode_id": e["id"], "oracle_decision_index": best,
                      "valid_states": int(e["valid"].sum()), "exit_labels": int(y[e["valid"]].sum())})
    x = torch.from_numpy(np.concatenate(xs))
    y = torch.from_numpy(np.concatenate(ys))
    positive_weight = (len(y) - y.sum()) / y.sum().clamp(min=1.)
    opt = torch.optim.AdamW(list(model.trunk.parameters()) + list(model.actor.parameters()), lr=cfg["mlp_lr"], weight_decay=cfg["weight_decay"])
    history = []
    for epoch in range(cfg["mlp_epochs"]):
        order = torch.randperm(len(x))
        losses = []
        for b in order.split(cfg["mlp_batch_size"]):
            logits, _ = model(x[b])
            loss = F.binary_cross_entropy_with_logits(logits, y[b], pos_weight=positive_weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        history.append({"epoch": epoch + 1, "bce": float(np.mean(losses))})
    return model, {"history": history, "train_states": len(x), "positive_class_weight": float(positive_weight), "oracle_audit": audit}


def return_to_go(terminal_reward, terminal_offset, decision_offsets, gamma):
    """Terminal net PnL at exact execution offset; discounts in elapsed hours."""
    return terminal_reward * gamma ** ((terminal_offset - np.asarray(decision_offsets)) / 12.)


def train_rl(eps, scaler, cfg, seed, initial=None):
    seed_all(seed + 10000)
    model = copy.deepcopy(initial) if initial is not None else ActorCritic(cfg)
    anchor = copy.deepcopy(initial).eval() if initial is not None else None
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["rl_lr"], weight_decay=cfg["weight_decay"])
    rng = np.random.default_rng(seed + 20000)
    history, total_decisions, total_visits = [], 0, 0
    for update in range(cfg["rl_updates"]):
        selected = rng.integers(len(eps), size=cfg["rl_batch_episodes"])
        batch = [eps[i] for i in selected]
        lengths = [len(e["x"]) for e in batch]
        if sum(lengths) == 0:
            raise ValueError("No decision states in batch")
        x = torch.from_numpy(np.concatenate([transform(e["x"], scaler) for e in batch]))
        logits, values = model(x)
        distribution = torch.distributions.Bernoulli(logits=logits)
        acts = distribution.sample()
        visited, allowed, targets = [], [], []
        cursor = 0
        net_rewards = []
        for e, n in zip(batch, lengths):
            local = acts[cursor:cursor + n].detach().numpy().astype(bool) & e["valid"]
            choices = np.flatnonzero(local)
            chosen = int(choices[0]) if len(choices) else n
            out = e["candidates"][chosen] if chosen < n else e["forced"]
            reward = 100 * cash_result(1., e["entry_raw"], out[1], cfg)["net_pnl"]
            # A terminal event can precede the next decision. All prior rewards are zero;
            # its exact offset is included rather than slicing a reward array by row count.
            seen = np.arange(n) <= chosen
            visited.extend(seen)
            allowed.extend(seen & e["valid"])
            targets.extend(return_to_go(reward, out[0], e["offsets"], cfg["gamma_per_hour"]))
            net_rewards.append(reward)
            cursor += n
        visited = torch.from_numpy(np.asarray(visited, dtype=bool))
        allowed = torch.from_numpy(np.asarray(allowed, dtype=bool))
        target = torch.tensor(np.asarray(targets), dtype=torch.float32)
        advantage = target - values.detach()
        if allowed.any():
            adv = advantage[allowed]
            adv = (adv - adv.mean()) / adv.std(unbiased=False).clamp(min=1e-6)
            policy_loss = -(distribution.log_prob(acts)[allowed] * adv).mean()
            entropy = distribution.entropy()[allowed].mean()
        else:
            policy_loss = logits.sum() * 0
            entropy = logits.sum() * 0
        value_loss = F.mse_loss(values[visited], target[visited]) if visited.any() else values.sum() * 0
        bc = logits.sum() * 0
        if anchor is not None and allowed.any():
            with torch.no_grad():
                ref, _ = anchor(x)
            bc = F.binary_cross_entropy_with_logits(logits[allowed], ref[allowed].sigmoid())
        loss = policy_loss + cfg["value_coef"] * value_loss - cfg["entropy_coef"] * entropy + cfg["bc_anchor_coef"] * bc
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), cfg["grad_clip"])
        opt.step()
        total_decisions += int(allowed.sum())
        total_visits += int(visited.sum())
        if (update + 1) % 10 == 0:
            history.append({"update": update + 1, "mean_episode_net_pct": float(np.mean(net_rewards)), "loss": float(loss.detach())})
    return model.eval(), {"history": history, "updates": cfg["rl_updates"],
            "sampled_episodes": cfg["rl_updates"] * cfg["rl_batch_episodes"],
            "slot_5m_environment_steps": cfg["rl_updates"] * cfg["rl_batch_episodes"] * cfg["max_hold_hours"] * 12,
            "valid_policy_decisions": total_decisions, "visited_hourly_states_including_forced_hold": total_visits,
            "bc_anchor": initial is not None}


def pick_exit(ep, policy, model=None, scaler=None, cfg=None):
    eligible = np.flatnonzero(ep["valid"])
    if policy == "fixed_12h":
        picks = [j for j in eligible if ep["offsets"][j] >= 12 * 12]
    elif policy == "fixed_max":
        picks = []
    else:
        if not len(ep["x"]):
            return ep["forced"]
        with torch.no_grad():
            logits, _ = model(torch.from_numpy(transform(ep["x"], scaler)))
        picks = [j for j in eligible if float(logits[j]) >= 0.]  # frozen threshold .5
    return ep["candidates"][picks[0]] if len(picks) else ep["forced"]


def series_metrics(equity, trades, initial):
    n = np.asarray(equity)
    with_initial = np.r_[initial, n]
    r = np.diff(with_initial) / with_initial[:-1]
    sigma = r.std(ddof=1)
    return {"net_return_pct": float((n[-1] / initial - 1) * 100),
            "gross_same_quantity_return_pct": float((n[-1] - initial + sum(t["cost"] for t in trades)) / initial * 100),
            "net_profit": float(n[-1] - initial),
            "max_drawdown_pct": float((1 - n / np.maximum.accumulate(np.r_[initial, n])[1:]).max() * 100),
            "sharpe_5m_365d": float(r.mean() / sigma * np.sqrt(365 * 288)) if sigma > 0 else 0.,
            "turnover_initial_capital": float(sum(t["turnover"] for t in trades) / initial),
            "total_cost": float(sum(t["cost"] for t in trades)), "trades": len(trades),
            "win_rate": float(np.mean([t["net_pnl"] > 0 for t in trades])) if trades else 0.}


def evaluate(data, eps, bounds, cfg, policy, model, scaler, output=None, cost_mult=1):
    lo, hi = bounds
    timelines, sleeve_paths, cash_paths, mkt_paths, trades = [], [], [], [], []
    perasset = {}
    initial = cfg["initial_cash"]
    for asset, d in data.items():
        times = d.open_ms.to_numpy()
        left, right = np.searchsorted(times, [lo, hi])
        count = right - left
        eq, ca, mv = np.full(count, initial / len(data)), np.full(count, initial / len(data)), np.zeros(count)
        balance = initial / len(data)
        previous_end = -1
        local_trades = []
        for ep in [e for e in eps if e["asset"] == asset]:
            start = ep["start"]
            if start <= previous_end:
                raise AssertionError("Overlapping capital slot")
            off, raw, reason = pick_exit(ep, policy, model, scaler, cfg)
            end = start + off
            result = cash_result(balance, ep["entry_raw"], raw, cfg, cost_mult)
            i, j = start - left, end - left
            marks = result["qty"] * d.close.to_numpy()[start:end]
            eq[i:j], ca[i:j], mv[i:j] = marks, 0., marks
            balance = result["end_cash"]
            eq[j:], ca[j:], mv[j:] = balance, balance, 0.
            # Open fills have exact open timestamps; intrabar stops have unknown 5m timing.
            is_intrabar = reason in ("stop", "take_profit")
            t = {"episode_id": ep["id"], "asset": asset, "entry_ms": ep["entry_ms"],
                 "exit_ms": int(times[end]), "entry_utc": utc(ep["entry_ms"]), "exit_bar_utc": utc(times[end]),
                 "exit_timing": "unknown_within_5m_bar" if is_intrabar else "bar_open",
                 "entry_raw": ep["entry_raw"], "exit_raw": raw, "exit_reason": reason,
                 "holding_bars": off, "capital_before": balance - result["net_pnl"], **result}
            local_trades.append(t)
            previous_end = end
        perasset[asset] = series_metrics(eq, local_trades, initial / len(data))
        perasset[asset]["exposure_mean"] = float(np.mean(mv / eq))
        timelines.append(times[left:right])
        sleeve_paths.append(eq)
        cash_paths.append(ca)
        mkt_paths.append(mv)
        trades.extend(local_trades)
    if not all(np.array_equal(timelines[0], t) for t in timelines):
        raise ValueError("Assets do not share evaluation calendar")
    equity = np.sum(sleeve_paths, axis=0)
    cash, market = np.sum(cash_paths, axis=0), np.sum(mkt_paths, axis=0)
    np.testing.assert_allclose(equity, cash + market, atol=1e-7)
    metrics = series_metrics(equity, trades, initial)
    metrics["exposure_mean"] = float(np.mean(market / equity))
    metrics["per_asset"] = perasset
    metrics["calendar_start"] = utc(lo)
    metrics["calendar_end_exclusive"] = utc(hi)
    if output:
        output.mkdir(parents=True, exist_ok=False)
        pd.DataFrame(trades).to_csv(output / "trades.csv", index=False)
        # Daily and 5m marks share the exact common calendar, including idle cash.
        pd.DataFrame({"bar_open_ms": timelines[0], "mark_available_ms": timelines[0] + BAR_MS,
                      "equity": equity, "cash": cash, "market_value": market,
                      "exposure": market / equity}).to_csv(output / "equity.csv", index=False, float_format="%.9f")
        dump(output / "metrics.json", metrics)
    return metrics, trades


def summarize(rows, output, cfg):
    flat = pd.DataFrame([{k: v for k, v in r.items() if k != "per_asset"} for r in rows])
    flat.to_csv(output / "metrics.csv", index=False)
    metrics = ["net_return_pct", "gross_same_quantity_return_pct", "max_drawdown_pct", "sharpe_5m_365d", "turnover_initial_capital", "total_cost", "exposure_mean", "trades"]
    summary = flat.groupby(["split", "stress", "policy"])[metrics].agg(["mean", "std", "median", "min", "max"])
    summary.to_csv(output / "seed_summary.csv")
    differences = []
    for (split, stress, seed), g in flat.groupby(["split", "stress", "seed"]):
        by = g.set_index("policy")
        for a, b in [("mlp_rl", "mlp"), ("mlp_rl", "raw_rl"), ("mlp", "fixed_max"), ("mlp_rl", "fixed_max")]:
            differences.append({"split": split, "stress": stress, "seed": int(seed), "a": a, "b": b,
                                **{m: float(by.loc[a, m] - by.loc[b, m]) for m in metrics}})
    pd.DataFrame(differences).to_csv(output / "paired_differences.csv", index=False)
    return flat


def paired_bootstrap(records, output, cfg):
    """Paired fixed-entry block bootstrap; exploratory and not adjusted for multiple tests."""
    rng = np.random.default_rng(45001)
    findings = []
    for seed, runs in records.items():
        base = {t["episode_id"]: t["net_pnl"] / t["capital_before"] for t in runs["mlp"]}
        alt = {t["episode_id"]: t["net_pnl"] / t["capital_before"] for t in runs["mlp_rl"]}
        # Pair both assets by entry date; 7 adjacent entry blocks ~14 days preserve dependence.
        days = {}
        for eid in base:
            days.setdefault(int(eid.split(":")[1]), []).append(100 * (alt[eid] - base[eid]))
        v = np.array([np.mean(days[t]) for t in sorted(days)])
        samples = []
        for _ in range(cfg["bootstrap_samples"]):
            starts = rng.integers(len(v), size=int(np.ceil(len(v) / 7)))
            ix = np.concatenate([(np.arange(7) + s) % len(v) for s in starts])[:len(v)]
            samples.append(float(v[ix].mean()))
        findings.append({"seed": seed, "mean_trade_return_difference_pp": float(v.mean()),
                         "ci95_low": float(np.quantile(samples, .025)), "ci95_high": float(np.quantile(samples, .975)),
                         "entry_blocks": len(v), "block_length": 7, "bootstrap_samples": cfg["bootstrap_samples"]})
    dump(output / "exploratory_block_bootstrap.json", findings)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    guard = memory_guard(cfg["memory_limit_mb"])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    start_clock = time.perf_counter()
    dump(output / "config.json", cfg)
    data_dir = (args.config.resolve().parent / cfg["data_dir"]).resolve()
    data = {a: load_data(data_dir / f"{a}-5m.csv") for a in cfg["assets"]}
    splits, edges, purged, mf = build_splits(data, cfg)
    scaler = fit_scaler(splits["train"])
    dump(output / "scaler.json", {k: v.tolist() for k, v in scaler.items()} | {"features": FEATURES, "fit_split": "train"})
    dump(output / "data_manifest.json", {a: {"path": str(data_dir / f"{a}-5m.csv"), "sha256": sha(data_dir / f"{a}-5m.csv"), "rows": len(d), "first_open": utc(d.open_ms.iloc[0]), "last_available": utc(d.available_ms.iloc[-1])} for a, d in data.items()})
    dump(output / "split_audit.json", {"edges_utc": [utc(t) for t in edges], "purged": purged, "counts": {s: len(es) for s, es in splits.items()}, "path_rule": "entry >= split start AND full max-hold terminal open < split end"})
    pd.DataFrame([{"split": s, "episode_id": e["id"], "entry_ms": e["entry_ms"], "full_path_end_ms": e["end_ms"], "states": len(e["x"])} for s, es in splits.items() for e in es]).to_csv(output / "episodes.csv", index=False)
    all_metrics, boot_records = [], {}
    for seed in cfg["seeds"]:
        sd = output / f"seed_{seed}"
        sd.mkdir()
        t0 = time.perf_counter()
        mlp, mlp_info = train_mlp(splits["train"], scaler, cfg, seed)
        mlp_info["seconds"] = time.perf_counter() - t0
        models = {"mlp": mlp.eval()}
        training = {"mlp": mlp_info}
        for name, initial in [("raw_rl", None), ("mlp_rl", mlp)]:
            t0 = time.perf_counter()
            models[name], info = train_rl(splits["train"], scaler, cfg, seed, initial)
            info["seconds"] = time.perf_counter() - t0
            training[name] = info
        dump(sd / "training.json", training)
        for name, m in models.items():
            torch.save({"state_dict": m.state_dict(), "seed": seed, "features": FEATURES,
                        "scaler": {k: v.tolist() for k, v in scaler.items()}, "config": cfg,
                        "checkpoint": "final_fixed_budget_no_test_selection"}, sd / f"{name}.pt")
        boot_records[seed] = {}
        for split, index in [("val", 1), ("test", 2)]:
            # No oracle labels are computed for val/test. Models and threshold are frozen.
            for stress, mult, delay in [("base", 1, 0), ("double_cost", 2, 0), ("delay_5m", 1, 1)]:
                if split == "val" and stress != "base":
                    continue
                eps = splits[split] if stress == "base" else [make_episode(data[e["asset"]], mf[e["asset"]], e["start"], cfg, e["asset"], mult, delay) for e in splits[split]]
                for name in ["fixed_max", "fixed_12h", "mlp", "raw_rl", "mlp_rl"]:
                    metrics, trades = evaluate(data, eps, edges[index:index + 2], cfg, name, models.get(name), scaler, sd / f"{split}_{stress}_{name}", mult)
                    all_metrics.append({"seed": seed, "split": split, "stress": stress, "policy": name, **metrics})
                    if split == "test" and stress == "base" and name in ("mlp", "mlp_rl"):
                        boot_records[seed][name] = trades
        print(f"seed={seed} complete; elapsed={time.perf_counter()-start_clock:.1f}s", flush=True)
    summarize(all_metrics, output, cfg)
    paired_bootstrap(boot_records, output, cfg)
    dump(output / "runtime.json", {"python": sys.version, "torch": torch.__version__, "numpy": np.__version__,
           "pandas": pd.__version__, "device": "cpu", "threads": torch.get_num_threads(), "memory_limit_mb": cfg["memory_limit_mb"],
           "memory_guard": guard, "rss_mb_end": psutil.Process().memory_info().rss / 2**20,
           "peak_working_set_mb": getattr(psutil.Process().memory_info(), "peak_wset", 0) / 2**20,
           "wall_seconds": time.perf_counter() - start_clock,
           "source_sha256": sha(__file__), "config_sha256": sha(args.config)})
    dump(output / "artifact_manifest.json", {str(f.relative_to(output)): sha(f) for f in sorted(output.rglob("*")) if f.is_file()})
    print(f"DONE {output}", flush=True)


if __name__ == "__main__":
    main()
