"""Causal features, fitted tabular RL, and self-financing minute replay.

These are independent adaptations, not the authors' RL algorithm or LLM agents.
All rates are fractions of notional. Orders are continuously divisible.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from numba import njit


def features(market, delay=1):
    close = pd.Series(market["close"])
    decisions = np.arange(1440, len(close)-delay, 5)
    # Information available at t consists only of candles ending at or before t.
    known = close.shift(1)
    one = np.log(known / known.shift(5))
    vol = one.rolling(288, min_periods=288).std()
    result = {"decision_index": decisions, "execution_index": decisions + delay}
    for horizon in (15, 30, 60, 240):
        z = np.log(known / known.shift(horizon)) / (vol * np.sqrt(horizon/5))
        result[f"z{horizon}"] = z.to_numpy()[decisions]
    result["vol"] = vol.to_numpy()[decisions]
    result["timestamp"] = market["timestamp"][decisions]
    if any(not np.all(np.isfinite(v)) for v in result.values()):
        raise ValueError("feature warmup insufficient")
    return result


def encode_state(feature, vol_threshold):
    a = np.digitize(feature["z60"], [-0.5, 0.5])
    b = np.digitize(feature["z240"], [-0.5, 0.5])
    c = (feature["vol"] > vol_threshold).astype(int)
    return (a * 3 + b) * 2 + c


def period_outcomes(market, execution):
    next_execution = np.r_[execution[1:], len(market["open"])]
    prices = np.r_[market["open"], market["close"][-1]]
    returns = prices[next_execution] / prices[execution] - 1
    # Funding at the next execution boundary belongs to the OLD position.
    cashflow = market["funding_rate"] * market["mark_open"]
    cumulative = np.r_[0., np.cumsum(cashflow)]
    # (execution, next_execution] with end-of-file exclusive and no synthetic event.
    stop = np.minimum(next_execution + 1, len(cashflow))
    funding = (cumulative[stop] - cumulative[execution+1]) / prices[execution]
    return returns, funding, next_execution


def fit_policy(state, returns, funding, train, actions, cost, risk, discount, max_iterations=2000):
    """Model-based fitted tabular Q iteration with previous action in the state.

    Sample mean is shrunk toward zero with 100 pseudo-observations. Transitions
    receive one unconditional-prior observation. No validation/PnL tuning.
    """
    n_states, n_actions = 18, len(actions)
    indices = np.flatnonzero(train)
    if not len(indices) or indices[-1] >= len(state)-1:
        raise ValueError("invalid training labels")
    counts = np.bincount(state[indices], minlength=n_states)
    avg = np.bincount(state[indices], weights=returns[indices], minlength=n_states)/(counts+100)
    avg_fund = np.bincount(state[indices], weights=funding[indices], minlength=n_states)/np.maximum(counts,1)
    second = np.bincount(state[indices], weights=returns[indices]**2, minlength=n_states)/np.maximum(counts,1)
    variance = np.maximum(second - avg**2, 1e-10)
    transition = np.zeros((n_states,n_states))
    np.add.at(transition, (state[indices],state[indices+1]), 1)
    prior = np.bincount(state[indices+1], minlength=n_states)/len(indices)
    transition = (transition+prior)/(counts[:,None]+1)
    # Gross arm deliberately omits funding as well as execution costs.
    expected = avg-avg_fund if cost > 0 else avg
    rewards = (expected[:,None,None]*actions[None,None,:]
               -risk*variance[:,None,None]*actions[None,None,:]**2
               -cost*np.abs(actions[None,:,None]-actions[None,None,:]))
    value = np.zeros((n_states,n_actions))
    residual = float("inf")
    for iteration in range(max_iterations):
        continuation = transition @ value
        q = rewards + discount*continuation[:,None,:]
        new = q.max(axis=2)
        residual = float(np.max(np.abs(new-value)))
        value = new
        if residual < 1e-10:
            break
    if residual >= 1e-8:
        raise ValueError(f"RL Bellman iteration did not converge: {residual}")
    # Prefer flat / lower exposure for numerical ties.
    q = rewards + discount*(transition @ value)[:,None,:]
    tie_penalty = np.abs(actions)*1e-13
    policy = np.argmax(q-tie_penalty[None,None,:],axis=2)
    return policy, dict(samples=len(indices), min_state_samples=int(counts.min()), max_state_samples=int(counts.max()), iterations=iteration+1, residual=residual, mean_abs_conditional_return=float(np.abs(avg).mean()))


@njit(cache=False)
def policy_targets(policy, state, actions, previous=2):
    out = np.empty(len(state))
    for i in range(len(state)):
        previous = policy[state[i],previous]
        out[i] = actions[previous]
    return out


@njit(cache=False)
def _replay(open_price, high, low, close, mark_open, mark_high, mark_low,
            funding, execution, targets, start, end, equity0, fee, slip, mmr, near_multiple,
            fixed_quantity):
    """Linear futures equity; fees/slippage charged on actual quantity changes.

    At the open: price PnL, Funding on the old quantity, maintenance on the
    old quantity at opening mark, then the elective order (only if solvent).
    Check the new position at opening mark before considering later extremes.
    Terminal position is closed at last close. This is a flat-rate OHLC proxy.
    """
    n = end-start
    equity_path = np.empty(n)
    exposure_path = np.empty(n)
    qty_path = np.empty(n)
    fees = slips = funding_paid = turnover = gross = shortfall = 0.
    equity, q, prev_price, k = equity0, 0., open_price[start], 0
    trades = near = liquidations = 0
    bankrupt = False
    for t in range(start,end):
        price = open_price[t]
        delta = q*(price-prev_price)
        equity += delta
        gross += delta
        paid = q*mark_open[t]*funding[t]
        equity -= paid
        funding_paid += paid
        opening_mark = mark_open[t]
        old_mark_equity = equity + q*(opening_mark-price)
        old_maintenance = abs(q)*opening_mark*mmr
        gap_breach = q != 0. and old_mark_equity <= old_maintenance
        near_open = q != 0. and old_mark_equity <= near_multiple*old_maintenance
        if k < len(execution) and t == execution[k]:
            target = targets[k]
            if not bankrupt and not gap_breach and (not fixed_quantity or k == 0):
                # Solve q_new = target * equity_after_cost / price exactly.
                direction = np.sign(target*equity/price-q)
                new_q = target*(equity/price+(fee+slip)*direction*q)/(1+target*(fee+slip)*direction)
                traded = abs(new_q-q)*price
                if traded > 1e-8:
                    trades += 1
                fees += traded*fee
                slips += traded*slip
                turnover += traded
                equity -= traded*(fee+slip)
                q = new_q
            k += 1
        # An opening breach is already observable: do not price its exit
        # at a low/high only known after the opening order.
        open_maintenance = abs(q)*opening_mark*mmr
        open_breach = q != 0. and equity+q*(opening_mark-price) <= open_maintenance
        adverse = opening_mark if open_breach else (mark_low[t] if q >= 0 else mark_high[t])
        stressed = equity+q*(adverse-price)
        maintenance = abs(q)*adverse*mmr
        if near_open or (q != 0. and stressed <= near_multiple*maintenance):
            near += 1
        if q != 0. and stressed <= maintenance:
            # Mark-price proxy fill, regular close costs and a zero equity floor.
            # Record the deficit absorbed by that modeling assumption separately.
            net_exit = stressed-abs(q)*adverse*(fee+slip)
            shortfall += max(0., -net_exit)
            equity = max(0., net_exit)
            gross += q*(adverse-price)
            fees += abs(q)*adverse*fee
            slips += abs(q)*adverse*slip
            turnover += abs(q)*adverse
            trades += 1
            q = 0.
            liquidations += 1
            bankrupt = True
        delta = q*(close[t]-price)
        equity += delta
        gross += delta
        prev_price = close[t]
        exposure_path[t-start] = abs(q)*close[t]/equity if equity > 0 else 0.
        qty_path[t-start] = q
        if t == end-1:
            traded = abs(q)*close[t]
            fees += traded*fee
            slips += traded*slip
            turnover += traded
            equity -= traded*(fee+slip)
            if traded > 1e-8:
                trades += 1
            q = 0.
        equity_path[t-start] = equity
    totals = np.array([equity,fees,slips,funding_paid,turnover,gross,trades,near,liquidations,shortfall])
    return equity_path,exposure_path,qty_path,totals


def replay(market, execution, targets, start, end, config, slip_bps=1., fixed_quantity=False):
    execution=np.asarray(execution,dtype=np.int64)
    targets=np.asarray(targets,dtype=float)
    if len(execution)!=len(targets) or np.any(np.diff(execution)<=0) or (len(execution) and (execution[0]<start or execution[-1]>=end)):
        raise ValueError("invalid order schedule")
    if not np.all(np.isfinite(targets)):
        raise ValueError("nonfinite target")
    return _replay(*(market[x] for x in ("open","high","low","close","mark_open","mark_high","mark_low","funding_rate")),execution,targets,start,end,
                   config["initial_equity"],config["fee_bps"]/10000,slip_bps/10000,
                   config["maintenance_margin_rate_proxy"],config["near_liquidation_margin_multiple"],fixed_quantity)


def performance(equity, exposure, totals, initial, timestamp):
    daily=pd.Series(equity,index=pd.to_datetime(timestamp,unit="ms",utc=True)).resample("1D").last()
    daily_returns=daily.pct_change().fillna(daily.iloc[0]/initial-1)
    peak=np.maximum.accumulate(np.r_[initial,equity])[1:]
    dd=equity/peak-1
    std=daily_returns.std(ddof=1)
    ordered=np.sort(daily_returns.to_numpy())
    tail=ordered[:max(1,int(np.ceil(len(ordered)*.05)))].mean()
    return dict(return_pct=100*(equity[-1]/initial-1), net_pnl_usdt=float(equity[-1]-initial),
                mdd_pct=float(-100*dd.min()), daily_sharpe=float(daily_returns.mean()/std*np.sqrt(365)) if std>1e-12 else None,
                daily_es05_pct=float(tail*100), fees_usdt=float(totals[1]), slippage_usdt=float(totals[2]),
                funding_paid_usdt=float(totals[3]), turnover_equity=float(totals[4]/initial), gross_pnl_usdt=float(totals[5]),
                order_count=int(totals[6]), near_liquidation_minutes=int(totals[7]), liquidation_proxy_count=int(totals[8]),
                liquidation_floor_credit_usdt=float(totals[9]),
                mean_abs_exposure=float(np.mean(exposure)),max_abs_exposure=float(np.max(exposure))),daily,daily_returns
