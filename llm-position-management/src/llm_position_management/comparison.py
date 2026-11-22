"""Frozen canary and independent-account comparison; no exchange orders."""
import argparse
from bisect import bisect_right
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from trading_core.market_data.binance_replay import load
from .cli import _account, _costs, _limits, _write_json, _write_jsonl
from .gemini_policy import GeminiPolicy, MODEL, INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION, PROMPT, response_schema
from .metrics import evaluate
from .policy import PolicyResponse
from .report import build_report
from .runner import PositionRunner, RunnerConfig
from .scripted_replay import LifecyclePolicy
from .store import RunStore

DATA_START, DATA_END = 1788220800000, 1788480000000
COMPARE_START = 1788393600000
SETTINGS = {"limits": {"exposure_anchor_usd": "250", "max_position_notional_usd": "250",
    "min_notional_usd": "10", "risk_per_position_pct": "1", "max_daily_loss_pct": "20",
    "max_drawdown_pct": "25", "max_hold_ms": 86_400_000},
    "costs": {"fee_rate": "0.00045", "spread_bps": "2", "slippage_bps": "1"},
    "initial_account": {"cash": "1000", "equity": "1000"}}


def feature_reader(ticks):
    times = [tick.timestamp_ms for tick in ticks]
    def features(timestamp):
        last = bisect_right(times, timestamp)-1
        values = {}
        for minutes in (5, 15, 60):
            if last >= minutes:
                values[f"return_{minutes}m_pct"] = str((ticks[last].close_price/ticks[last-minutes].close_price-1)*100)
        values["recent_completed_5m_closes"] = [str(ticks[i].close_price) for i in range(max(0,last-30),last+1,5)]
        return values
    return features


class ControlledPolicy:
    def __init__(self, policy, end, no_trade=False):
        self.policy, self.end, self.no_trade = policy, end, no_trade

    def reserve_cost_usd(self, observation, request_id):
        if self.no_trade or observation["as_of_ms"] >= self.end-300_000:
            return Decimal(0)
        return self.policy.reserve_cost_usd(observation, request_id)

    def decide(self, observation, request_id):
        if self.no_trade or observation["as_of_ms"] >= self.end-300_000:
            closing = observation["as_of_ms"] >= self.end-300_000
            return PolicyResponse({"schema_version": 1, "decision_id": request_id,
                "intent": "set_target" if closing else "hold", "target_fraction": "0" if closing else None,
                "stop_price": None, "thesis": "fixed terminal close" if closing else "no-trade control",
                "invalidation": "fixed experiment boundary"}, int(observation["as_of_ms"])+(0 if observation["as_of_ms"]==self.end else 1), Decimal(0))
        response = self.policy.decide(observation, request_id)
        # Same minimum positive latency and next-completed-minute fill for all arms.
        return PolicyResponse(response.payload, max(response.received_at_ms,int(observation["as_of_ms"])+1), response.estimated_cost_usd)


def run(data: Path, output: Path, arm: str, budget: Decimal, max_slots: int, api_ledger: Path | None = None):
    if budget <= 0 or budget > 10:
        raise ValueError("authorized shared budget is at most 10 USD")
    if arm not in {"no_trade", "fixed_rule"}:
        from dotenv import dotenv_values
        if not (os.environ.get("GEMINI_API_KEY") or dotenv_values(".env").get("GEMINI_API_KEY")):
            raise ValueError("Gemini API key unavailable before starting run")
    ticks, funding, identity = load(data, DATA_START, DATA_END)
    api_directory = api_ledger or output / "api"
    run_id = "protocol-v3-" + arm
    canary = arm == "canary"
    start = DATA_START + 8*3_600_000 if canary else COMPARE_START
    end = start + 65*60_000 if canary else DATA_END
    selected = [tick for tick in ticks if start < tick.timestamp_ms <= end]
    experiment = {"version": 3, "data": identity, "budget_usd": str(budget), "settings": SETTINGS,
        "model": MODEL, "input_usd_per_million": str(INPUT_USD_PER_MILLION),
        "output_usd_per_million": str(OUTPUT_USD_PER_MILLION), "thinking": "minimal", "max_output_tokens": 512,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps([response_schema(r,p) for r in (False,True) for p in (False,True)],sort_keys=True).encode()).hexdigest(),
        "api_ledger": str(api_directory),
        "canary_start": DATA_START+8*3_600_000, "comparison_start": COMPARE_START, "comparison_end": DATA_END,
        "arms": ["no_trade", "fixed_rule", "llm_no_resize", "llm_resize"],
        "adoption": "inconclusive; one day cannot establish edge", "funding_order": "bar-end stop before funding; intrabar adverse bound flagged"}
    output.mkdir(parents=True, exist_ok=True)
    freeze = output / "freeze.json"
    if freeze.exists() and json.loads(freeze.read_text(encoding="utf-8")) != experiment:
        raise ValueError("frozen experiment mismatch")
    if not freeze.exists():
        _write_json(freeze, experiment)
    if arm.startswith("llm"):
        canary_path = output / "canary" / "report.json"
        if not canary_path.exists() or json.loads(canary_path.read_text())["canary_gate"] != "passed":
            raise ValueError("canary gate not passed")
    directory = output / arm
    directory.mkdir(exist_ok=True)
    store = RunStore(directory / "run.db")
    fingerprint = hashlib.sha256(json.dumps(experiment, sort_keys=True).encode()).hexdigest()
    state = store.load_state(run_id)
    remaining = selected if state is None else [tick for tick in selected if tick.timestamp_ms >= state.tick.timestamp_ms]
    if max_slots and len(remaining) > max_slots*5+1:
        remaining = remaining[:max_slots*5+1]
    if arm in {"no_trade", "fixed_rule"}:
        policy = LifecyclePolicy(start, end)
    else:
        policy = GeminiPolicy(api_directory, budget, arm != "llm_no_resize", feature_reader(ticks))
    runner = PositionRunner(initial=_account(SETTINGS["initial_account"]), limits=_limits(SETTINGS["limits"]),
        costs=_costs(SETTINGS["costs"]), config=RunnerConfig(run_id,300_000,60_000,budget),
        policy=ControlledPolicy(policy,end,no_trade=arm=="no_trade"), store=store, config_fingerprint=fingerprint)
    result = runner.run(remaining, funding_by_timestamp_ms=funding)
    observed = [tick for tick in selected if tick.timestamp_ms <= remaining[-1].timestamp_ms]
    metrics = evaluate(result, observed, _costs(SETTINGS["costs"]))
    curve = metrics.pop("equity_curve")
    complete = remaining[-1].timestamp_ms == end
    report = build_report(result) | metrics | {"period_complete": complete, "arm": arm,
        "observed_last_ms": remaining[-1].timestamp_ms, "start_ms": start, "end_ms": end}
    if canary:
        calls = [json.loads(path.read_text(encoding="utf-8")) for path in api_directory.glob("*.json")]
        calls = [call for call in calls if call["request"]["decision_id"].startswith(run_id+":")]
        report["latency_ms"] = [call["latency_ms"] for call in calls]
        report["canary_gate"] = "passed" if complete and len(calls)>=10 and metrics["quality"]=="ok" else "pending_or_failed"
    _write_json(directory/"report.json",report)
    _write_jsonl(directory/"equity.jsonl",curve)
    _write_jsonl(directory/"decisions.jsonl",list(result.records))
    _write_jsonl(directory/"events.jsonl",list(result.events))
    print(json.dumps({key:value for key,value in report.items() if key not in {"holding_ms","lifecycle_pnl"}},sort_keys=True),flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=["canary","no_trade","fixed_rule","llm_no_resize","llm_resize"],required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--api-ledger", type=Path)
    parser.add_argument("--max-slots", type=int, default=36)
    run(**vars(parser.parse_args()))
