"""Real-data functional acceptance replay, not a candidate trading strategy."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from trading_core.market_data.replay_inputs import load_replay_inputs
from .cli import _account, _costs, _limits, _write_json, _write_jsonl
from .policy import PolicyResponse
from .report import build_report
from .runner import PositionRunner, RunnerConfig
from .store import RunStore


class LifecyclePolicy:
    """Fixed six-hour alternating direction script using only current observation."""
    def __init__(self, start: int, end: int):
        self.start, self.end = start, end

    def reserve_cost_usd(self, observation, request_id):
        return Decimal("0")

    def decide(self, observation, request_id):
        timestamp = int(observation["as_of_ms"])
        slot = (timestamp - self.start) // 300_000
        phase = slot % 72
        direction = Decimal("1") if slot // 72 % 2 == 0 else Decimal("-1")
        quantity = Decimal(observation["position"]["signed_quantity"])
        fraction = None
        if timestamp >= self.end - 300_000 or phase == 71:
            fraction = Decimal("0")
        elif phase == 1:
            fraction = direction * Decimal("0.5")
        elif phase == 3 and quantity:
            fraction = direction
        elif phase == 60 and quantity:
            fraction = direction * Decimal("0.25")
        stop = observation["position"]["stop_price"]
        if fraction and not quantity:
            stop = str(Decimal(observation["mark_price"]) * (1 - direction * Decimal("0.02")))
        payload = {"schema_version": 1, "decision_id": request_id,
                   "intent": "hold" if fraction is None else "set_target",
                   "target_fraction": str(fraction) if fraction is not None else None,
                   "stop_price": stop if fraction else None,
                   "thesis": "fixed functional lifecycle", "invalidation": "fixed 2 percent stop"}
        return PolicyResponse(payload, timestamp, Decimal("0"))


def replay(candles: Path, funding: Path, start: int, end: int, output: Path, resume_incomplete: Path | None = None):
    if output.exists():
        raise ValueError("refusing to overwrite existing replay")
    ticks, rates, inputs = load_replay_inputs(candles, funding, start, end)
    settings = {"limits": {"exposure_anchor_usd": "250", "max_position_notional_usd": "250",
                "min_notional_usd": "10", "risk_per_position_pct": "1", "max_daily_loss_pct": "20",
                "max_drawdown_pct": "25", "max_hold_ms": 86_400_000},
                "costs": {"fee_rate": "0.00045", "spread_bps": "2", "slippage_bps": "1"},
                "initial_account": {"cash": "1000", "equity": "1000"},
                "policy": "six-hour-lifecycle-v1", "inputs": inputs}
    fingerprint = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    temporary = resume_incomplete or output.with_name(f".{output.name}.tmp-{uuid4().hex}")
    if resume_incomplete:
        if (temporary.resolve().parent != output.resolve().parent
                or not temporary.name.startswith(f".{output.name}.tmp-")
                or not (temporary / "run.db").is_file()):
            raise ValueError("resume directory is not an incomplete run for this output")
    else:
        temporary.mkdir(parents=True)

    def runner(path):
        return PositionRunner(initial=_account(settings["initial_account"]), limits=_limits(settings["limits"]),
            costs=_costs(settings["costs"]), config=RunnerConfig("scripted", 300_000, 60_000, Decimal("0")),
            policy=LifecyclePolicy(start, end), store=RunStore(path), config_fingerprint=fingerprint)

    def continue_run(path):
        active = runner(path)  # verifies the immutable input/config fingerprint
        state = RunStore(path).load_state("scripted")
        remaining = ticks if state is None else [tick for tick in ticks if tick.timestamp_ms >= state.tick.timestamp_ms]
        return active.run(remaining, funding_by_timestamp_ms=rates)

    if resume_incomplete:
        result = continue_run(temporary / "run.db")
        resumed = continue_run(temporary / "resumed.db")
    else:
        result = runner(temporary / "run.db").run(ticks, funding_by_timestamp_ms=rates)
        split = len(ticks) // 2
        runner(temporary / "resumed.db").run(ticks[:split], funding_by_timestamp_ms=rates)
        resumed = runner(temporary / "resumed.db").run(ticks[split - 1:], funding_by_timestamp_ms=rates)
    if resumed != result:
        raise ValueError("restart with overlapping tick differs from uninterrupted replay")
    report = build_report(result)
    kinds = Counter(event.kind for event in result.events)
    actions = Counter()
    for record in result.records:
        plan = record.plan
        if plan is None or plan.status != "planned":
            continue
        before = Decimal(record.observation["position"]["signed_quantity"])
        actions["close" if not plan.target_quantity else "open" if not before else "reduce" if plan.reduce_only else "add"] += 1
    funding_events = [event for event in result.events if event.kind == "funding"]
    for event in funding_events:
        if event.amount != -event.quantity * event.price * rates[event.timestamp_ms]:
            raise ValueError("Funding ledger mismatch")
    snapshot = result.final_snapshot
    reconciled = Decimal("1000") + snapshot.realized_pnl - snapshot.fees_paid + snapshot.funding_paid + snapshot.unrealized_pnl
    if abs(reconciled - snapshot.equity) > Decimal("1e-20"):
        raise ValueError("equity ledger mismatch")
    if (report["status"] != "ok" or snapshot.signed_quantity or not funding_events
            or not all(actions[action] for action in ("open", "add", "reduce", "close"))):
        raise ValueError("functional acceptance criteria not met")
    report.update({"inputs": inputs, "actions": dict(actions), "event_counts": dict(kinds),
                   "restart_equal": True, "equity_reconciled": True,
                   "purpose": "functional_only_not_profitability", "config_sha256": fingerprint})
    _write_json(temporary / "config.json", settings)
    _write_json(temporary / "report.json", report)
    _write_jsonl(temporary / "decisions.jsonl", list(result.records))
    _write_jsonl(temporary / "events.jsonl", list(result.events))
    _write_json(temporary / "run_manifest.json", {"status": "complete", "config_sha256": fingerprint})
    temporary.replace(output)
    return {key: value for key, value in report.items() if key != "inputs"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-incomplete", type=Path)
    for name in ("candles", "funding", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("start", "end"):
        parser.add_argument(f"--{name}", type=int, required=True, help="UTC epoch milliseconds")
    args = parser.parse_args()
    print(json.dumps(replay(**vars(args)), sort_keys=True))


if __name__ == "__main__":
    main()
