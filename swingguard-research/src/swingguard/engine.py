"""One causal, cash-only two-asset execution engine shared by every arm."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import math
import statistics

from .data import BAR_MS, DAY_MS, validate_bars
from .providers import proposal_valid, validate_weights
from .risk import RiskController

ZERO, ONE = Decimal(0), Decimal(1)


def market_features(data, index):
    """Only day closes strictly preceding the decision boundary are observed."""
    if index < 60 * (DAY_MS // BAR_MS):
        raise ValueError("60 complete warmup days required")
    features = {}
    for symbol, bars in data.items():
        closes = [float(bars[index - 1 - day * 288].close) for day in range(60)][::-1]
        returns = [math.log(b / a) for a, b in zip(closes[-21:-1], closes[-20:])]
        features[symbol] = {"close": closes[-1], "sma20": statistics.mean(closes[-20:]),
                            "sma60": statistics.mean(closes), "return1d": closes[-1] / closes[-2] - 1,
                            "return7d": closes[-1] / closes[-8] - 1,
                            "return30d": closes[-1] / closes[-31] - 1,
                            "daily_vol20": statistics.stdev(returns),
                            "latest_bar_available_ms": bars[index - 1].available_ms}
    return features


class Portfolio:
    def __init__(self, symbols, config):
        self.cash = Decimal(config["initial_cash"])
        self.quantities = {s: ZERO for s in symbols}
        self.average = {s: ZERO for s in symbols}
        self.stops, self.targets = {}, {}
        self.fee_rate = Decimal(config["commission_bps"]) / 10_000
        self.slip_rate = Decimal(config["slippage_bps"]) / 10_000
        self.stop_pct = Decimal(config["stop_loss_pct"])
        self.tp_pct = Decimal(config["take_profit_pct"])
        self.fees = self.slippage = self.turnover = ZERO
        self.trades, self.closed_lifecycles = [], 0
        self.ambiguous_bars = 0

    def equity(self, prices):
        return self.cash + sum((self.quantities[s] * prices[s] for s in self.quantities), ZERO)

    def exposure(self, prices):
        eq = self.equity(prices)
        return sum((self.quantities[s] * prices[s] for s in self.quantities), ZERO) / eq if eq > 0 else ZERO

    def fill(self, symbol, quantity, reference, timestamp, reason, bar_open_ms, timing="open"):
        if abs(quantity) < Decimal("1e-18"):
            return
        if quantity < 0:
            quantity = max(quantity, -self.quantities[symbol])
        price = reference * (ONE + self.slip_rate if quantity > 0 else ONE - self.slip_rate)
        notional = abs(quantity) * price
        fee = notional * self.fee_rate
        if quantity > 0 and notional + fee > self.cash + Decimal("1e-16"):
            raise ValueError("cash-only execution forbids borrowing")
        old_qty = self.quantities[symbol]
        new_qty = old_qty + quantity
        if quantity > 0:
            self.average[symbol] = (old_qty * self.average[symbol] + quantity * price) / new_qty
            next_stop = self.average[symbol] * (ONE - self.stop_pct)
            self.stops[symbol] = max(self.stops.get(symbol, ZERO), next_stop)
            self.targets.setdefault(symbol, self.average[symbol] * (ONE + self.tp_pct))
        self.cash -= quantity * price + fee
        self.quantities[symbol] = new_qty
        if not new_qty:
            self.average[symbol] = ZERO
            self.stops.pop(symbol, None)
            self.targets.pop(symbol, None)
            if old_qty:
                self.closed_lifecycles += 1
        self.fees += fee
        self.slippage += abs(quantity) * reference * self.slip_rate
        self.turnover += notional
        self.trades.append({"event_time_ms": timestamp, "bar_open_ms": bar_open_ms, "timing": timing,
                            "symbol": symbol, "side": "buy" if quantity > 0 else "sell", "quantity": str(abs(quantity)),
                            "reference_price": str(reference), "fill_price": str(price), "notional": str(notional),
                            "commission": str(fee), "slippage_cost": str(abs(quantity) * reference * self.slip_rate),
                            "reason": reason, "cash_after": str(self.cash), "quantity_after": str(new_qty)})

    def rebalance(self, weights, decision_equity, decision_prices, bars, reason, *, reductions_only=False):
        # Quantities are fixed using the last known close, before the execution open.
        desired = {s: decision_equity * weights[s] / decision_prices[s] for s in self.quantities}
        if reductions_only:
            desired = {s: min(qty, self.quantities[s]) for s, qty in desired.items()}
        for symbol in self.quantities:
            delta = desired[symbol] - self.quantities[symbol]
            if delta < 0:
                bar = bars[symbol]
                self.fill(symbol, delta, bar.open, bar.open_ms, reason, bar.open_ms)
        buys = {s: max(ZERO, desired[s] - self.quantities[s]) for s in self.quantities}
        need = sum((q * bars[s].open * (ONE + self.slip_rate) * (ONE + self.fee_rate) for s, q in buys.items()), ZERO)
        scale = min(ONE, self.cash / need) if need else ZERO
        # A microscopic conservative reserve avoids Decimal roundoff creating negative cash.
        scale *= Decimal("0.999999999999999999999999999")
        for symbol, qty in buys.items():
            bar = bars[symbol]
            self.fill(symbol, qty * scale, bar.open, bar.open_ms, reason, bar.open_ms)
        if self.cash < -Decimal("1e-16"):
            raise ValueError("negative cash")

    def protect(self, bars, locked, *, gaps_only=False):
        for symbol, bar in bars.items():
            qty = self.quantities[symbol]
            if not qty:
                continue
            stop, target = self.stops[symbol], self.targets[symbol]
            reference = reason = None
            if bar.open <= stop:
                reference, reason = bar.open, "stop_gap"
            elif bar.open >= target:
                reference, reason = target, "take_profit_gap_conservative"
            elif not gaps_only:
                hit_stop, hit_target = bar.low <= stop, bar.high >= target
                if hit_stop:
                    reference, reason = stop, "stop_first_ambiguous" if hit_target else "stop"
                    self.ambiguous_bars += int(hit_target)
                elif hit_target:
                    reference, reason = target, "take_profit"
            if reason:
                self.fill(symbol, -qty, reference, bar.open_ms if gaps_only else bar.available_ms,
                          reason, bar.open_ms, "open" if gaps_only else "intrabar_unknown")
                locked.add(symbol)


def run_engine(data, config, start, end, arm, provider, features_fn=market_features):
    symbols = config["symbols"]
    first = data[symbols[0]][0].open_ms
    last = data[symbols[0]][-1].available_ms
    for symbol in symbols:
        validate_bars(data[symbol], first, last)
    if start % DAY_MS or end % DAY_MS or not first + BAR_MS <= start < end <= last:
        raise ValueError("invalid UTC evaluation bounds or missing preceding bar")
    account = Portfolio(symbols, config)
    controller = RiskController(config["risk"], account.cash)
    start_index, end_index = (start - first) // BAR_MS, (end - first) // BAR_MS
    active = pending = None
    pending_as_of = None
    pending_unlock = set()
    locked, state_counts = set(), Counter()
    curves, transitions, decisions = [], [], []
    day_equity, previous_day = account.cash, start // DAY_MS
    renewal_started_at = None
    grace_ms = config.get("proposal_renewal_grace_ms", 900_000)
    latency_limit_ms = config.get("max_proposal_latency_ms", 900_000)
    if not 0 <= grace_ms <= 900_000 or not 0 <= latency_limit_ms <= grace_ms:
        raise ValueError("renewal/latency must be bounded by fifteen minutes")
    previous_cap, previous_state = ONE, "NORMAL"
    api_cost = ZERO
    for index in range(start_index, end_index):
        bars = {s: data[s][index] for s in symbols}
        timestamp = bars[symbols[0]].open_ms
        past_prices = {s: data[s][index - 1].close for s in symbols}
        decision_equity = account.equity(past_prices)
        day = timestamp // DAY_MS
        rollover = day != previous_day
        basis_day = previous_day
        market_drop = max((max(0, 1 - float(data[s][index - 1].close / data[s][index - 13].close))
                           for s in symbols), default=0) if index >= 13 else 0
        if arm in ("pre_risk", "post_only"):
            context = controller.update(timestamp, decision_equity, day_equity, market_drop)
            context.update(daily_loss_basis_start_ms=basis_day * DAY_MS,
                           completed_day_loss=context["daily_loss"] if rollover else None,
                           current_day_loss=0 if rollover else context["daily_loss"])
            cap, state = controller.cap, controller.state
        else:
            cap, state, context = ONE, "BENCHMARK" if arm == "buy_hold" else "DISABLED", None
        # The just-completed bar belongs to the old UTC day at midnight.
        # Reset only AFTER its loss and recovery conditions were assessed once.
        if rollover:
            day_equity, previous_day = decision_equity, day
        state_counts[state] += 1
        changed = cap != previous_cap or state != previous_state
        if changed:
            transitions.append({"time_ms": timestamp, "state": state, "cap": str(cap), "context": context})
        previous_cap, previous_state = cap, state
        daily = timestamp % DAY_MS == 0
        if daily:
            if arm != "buy_hold":
                renewal_started_at = timestamp
                pending_unlock = locked.copy()
                features = features_fn(data, index)
                pending = provider.propose(timestamp, features, context if arm == "pre_risk" else None)
                pending_as_of = timestamp
                if pending is not None:
                    api_cost += Decimal(pending.cost_usd)
                    decisions.append({"as_of_ms": timestamp, "input_hash": pending.input_hash,
                                  "status": pending.status, "weights": pending.weights,
                                  "risk_supplied": arm == "pre_risk", "model": pending.model,
                                  "received_ms": pending.received_ms, "expires_ms": pending.expires_ms})
                else:
                    decisions.append({"as_of_ms": timestamp, "status": "response_unavailable_bounded_wait"})
        activated = False
        response_failed = False
        if pending is not None and pending.received_ms <= timestamp:
            if (proposal_valid(pending, pending_as_of, timestamp, config["proposal_ttl_ms"], latency_limit_ms)
                    and pending.status == "ok"):
                validate_weights(pending.weights, symbols)
                active, activated = pending, True
                # A stop AFTER the request must not be erased by its delayed response.
                locked.difference_update(pending_unlock)
            else:
                active = None
                response_failed = True
                decisions.append({"as_of_ms": timestamp, "status": "stale_or_expired_flat_fallback"})
            pending = None
        elif pending is not None and timestamp >= pending_as_of + latency_limit_ms:
            pending, active, response_failed = None, None, True
            decisions.append({"as_of_ms": timestamp, "status": "response_deadline_flat_fallback"})
        in_grace = (active is not None and timestamp >= active.expires_ms
                    and renewal_started_at is not None and active.expires_ms <= renewal_started_at
                    and timestamp < active.expires_ms + grace_ms
                    and timestamp < renewal_started_at + latency_limit_ms)
        expired = active is not None and timestamp >= active.expires_ms and not in_grace
        if expired:
            active = None
        if arm == "buy_hold":
            weights = {s: ONE / len(symbols) for s in symbols}
            rebalance = index == start_index
        else:
            weights = validate_weights(active.weights, symbols) if active is not None else {s: ZERO for s in symbols}
            total = sum(weights.values())
            factor = min(ONE, cap / total) if total else ZERO
            weights = {s: weight * factor for s, weight in weights.items()}
            drift = cap < ONE and account.exposure(past_prices) > cap + Decimal("0.005")
            rebalance = activated or changed or expired or response_failed or drift or (active is None and any(account.quantities.values()))
        if arm != "buy_hold":
            # Resting protective orders have priority over new orders at a gap open.
            account.protect(bars, locked, gaps_only=True)
        if rebalance:
            if arm != "buy_hold":
                weights = {s: ZERO if s in locked else w for s, w in weights.items()}
            reason = "initial_buy_hold" if arm == "buy_hold" else (
                "daily_proposal" if activated else "expired_signal" if expired else
                "failed_response" if response_failed else "risk_control")
            account.rebalance(weights, decision_equity, past_prices, bars, reason, reductions_only=in_grace)
        if arm != "buy_hold":
            account.protect(bars, locked)
        close_prices = {s: bar.close for s, bar in bars.items()}
        equity = account.equity(close_prices)
        if equity <= 0:
            raise ValueError("nonpositive equity")
        curves.append({"time_ms": timestamp + BAR_MS, "equity": str(equity), "cash": str(account.cash),
                       "exposure": str(account.exposure(close_prices)), "state": state,
                       "risk_cap": str(cap), "proposal_grace": in_grace,
                       **{s + "_quantity": str(account.quantities[s]) for s in symbols}})
    # All arms pay the same terminal exit costs at the final observed close.
    close_prices = {s: data[s][end_index - 1].close for s in symbols}
    for symbol in symbols:
        account.fill(symbol, -account.quantities[symbol], close_prices[symbol], end,
                     "terminal_close", end - BAR_MS, "final_known_close")
    curves[-1].update(equity=str(account.cash), cash=str(account.cash), exposure="0",
                      **{s + "_quantity": "0" for s in symbols})
    return {"arm": arm, "equity": curves, "trades": account.trades, "decisions": decisions,
            "transitions": transitions, "metrics": metrics(account, curves, config, api_cost),
            "state_bars": dict(state_counts)}


def metrics(account, curves, config, api_cost):
    initial = Decimal(config["initial_cash"])
    daily_values = [float(initial)]
    for index, row in enumerate(curves):
        if int(row["time_ms"]) % DAY_MS == 0 or index == len(curves) - 1:
            daily_values.append(float(row["equity"]))
    daily_returns = [b / a - 1 for a, b in zip(daily_values, daily_values[1:])]
    peak, max_dd = float(initial), 0
    for row in curves:
        value = float(row["equity"])
        peak = max(peak, value)
        max_dd = max(max_dd, 1 - value / peak)
    sigma = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
    return {"final_equity": float(account.cash), "profit": float(account.cash - initial),
            "return_pct": float((account.cash / initial - ONE) * 100), "max_drawdown_pct": max_dd * 100,
            "daily_sharpe_365_rf0": statistics.mean(daily_returns) / sigma * math.sqrt(365) if sigma else None,
            "turnover_notional": float(account.turnover), "turnover_over_initial": float(account.turnover / initial),
            "fill_count": len(account.trades), "closed_position_lifecycles": account.closed_lifecycles,
            "commission": float(account.fees), "slippage_cost": float(account.slippage),
            "mean_exposure_pct": statistics.mean(float(row["exposure"]) for row in curves) * 100,
            "ambiguous_stop_target_bars": account.ambiguous_bars,
            "modeled_standalone_api_cost": float(api_cost),
            "profit_after_modeled_api": float(account.cash - initial - api_cost)}
