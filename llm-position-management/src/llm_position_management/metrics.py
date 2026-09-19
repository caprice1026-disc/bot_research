"""Independent event-ledger metrics, with costs already included in equity."""
from decimal import Decimal
from collections import Counter
from statistics import median


def evaluate(result, ticks, costs, initial=Decimal("1000")):
    cash, quantity, average = initial, Decimal(0), Decimal(0)
    peak, max_dd, turnover, spread, slippage = initial, Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    api_peak, api_dd, cumulative_api, record_index = initial, Decimal(0), Decimal(0), 0
    holds, lifecycles, curve = [], [], []
    opened, entry_equity = None, None
    events = list(result.events)
    index = 0
    # Event order within each completed candle is authoritative, including conservative ambiguities.
    for tick in ticks:
        while index < len(events) and events[index].timestamp_ms <= tick.timestamp_ms:
            event = events[index]
            index += 1
            if event.kind == "funding":
                cash += event.amount
            elif event.kind == "fill":
                delta, price = event.quantity, event.price
                if not quantity:
                    opened, entry_equity = event.timestamp_ms, cash
                raw = price / (1 + (1 if delta > 0 else -1) * costs.adverse_bps / 10000)
                turnover += abs(delta) * raw
                spread += abs(delta) * raw * costs.spread_bps / 20000
                slippage += abs(delta) * raw * costs.slippage_bps / 10000
                if not quantity or (quantity > 0) == (delta > 0):
                    average = (abs(quantity)*average + abs(delta)*price) / (abs(quantity)+abs(delta))
                else:
                    cash += (price-average)*abs(delta)*(1 if quantity > 0 else -1)
                quantity += delta
                cash += event.amount
                if not quantity:
                    holds.append(event.timestamp_ms-opened)
                    lifecycles.append(cash-entry_equity)
                    opened = None
            equity = cash + (tick.close_price-average)*quantity
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak-equity)/peak*100)
        equity = cash + (tick.close_price-average)*quantity
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak*100)
        while record_index < len(result.records) and result.records[record_index].execution_timestamp_ms <= tick.timestamp_ms:
            cumulative_api += result.records[record_index].model_cost_usd
            record_index += 1
        api_equity = equity-cumulative_api
        api_peak = max(api_peak,api_equity)
        api_dd = max(api_dd,(api_peak-api_equity)/api_peak*100)
        curve.append({"timestamp_ms": tick.timestamp_ms, "equity": str(equity), "equity_after_api": str(api_equity)})
    if abs(equity-result.final_snapshot.equity) > Decimal("1e-18"):
        raise ValueError("independent event ledger does not reconcile")
    ambiguities = sum(e.kind == "funding_stop_ambiguous" for e in events)
    failures = sum(r.status in {"invalid_response", "stale_response", "model_request_unknown",
                               "model_budget_exhausted", "execution_unavailable", "forced_safe_close",
                               "safe_close_pending", "invalid_response_cost", "invalid_cost_reservation"} for r in result.records)
    fees = result.final_snapshot.fees_paid
    net_funding_cost = -result.final_snapshot.funding_paid
    actions = Counter()
    for record in result.records:
        plan = record.plan
        if plan is not None and plan.status == "planned":
            before = Decimal(record.observation["position"]["signed_quantity"])
            actions["close" if not plan.target_quantity else "open" if not before else "reduce" if plan.reduce_only else "add"] += 1
        elif record.status == "hold":
            actions["hold"] += 1
    return {"net_pnl": str(equity-initial), "net_after_api": str(equity-initial-result.model_cost_usd),
            "actions": dict(actions),
            "max_drawdown_pct": str(max_dd), "max_drawdown_after_api_pct": str(api_dd), "turnover_usd": str(turnover),
            "turnover_over_initial_equity": str(turnover/initial), "fees": str(fees),
            "spread_cost": str(spread), "slippage_cost": str(slippage),
            "net_funding_cost": str(net_funding_cost), "api_cost": str(result.model_cost_usd),
            "total_cost": str(fees+spread+slippage+net_funding_cost+result.model_cost_usd),
            "completed_lifecycles": len(holds), "holding_ms": holds,
            "median_holding_ms": median(holds) if holds else None,
            "open_holding_ms": ticks[-1].timestamp_ms-opened if opened is not None else None,
            "lifecycle_pnl": [str(value) for value in lifecycles], "funding_order_ambiguities": ambiguities,
            "response_failures": failures, "equity_curve": curve,
            "quality": "partial" if ambiguities or failures or not result.market_data_complete or result.pending_safe_close else "ok",
            "research_conclusion": "inconclusive_short_single_period"}
