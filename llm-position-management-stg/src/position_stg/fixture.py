"""Deterministic no-network lifecycle used by CLI and integration tests."""
from copy import deepcopy
from .runner import Runner
from .config import dec


class FixtureMarket:
    def __init__(self, clock):
        self.clock, self.mark = clock, 50000

    def snapshot(self):
        return {"mark": str(self.mark), "at": self.clock(), "received_at": self.clock(), "step": "0.00001",
                "bids": [{"px": str(self.mark - 1), "sz": "10"}],
                "asks": [{"px": str(self.mark + 1), "sz": "10"}], "funding_rate": "0"}

    def history(self, since, until):
        return ([{"t": t * 1000, "o": str(self.mark), "h": str(self.mark + 5), "l": str(self.mark - 5),
                  "c": str(self.mark), "v": "1"} for t in range(int(since // 60) * 60, int(until // 60) * 60, 60)], [])

    def features(self, now):
        return {interval: {"bars": [], "sma20": "50010", "sma50": "50000", "atr14": "300", "return": "0"}
                for interval in ("5m", "1h", "4h")}


class FixturePolicy:
    def __init__(self):
        self.calls = []
        self.target = "1"

    def call(self, role, request_id, inputs):
        self.calls.append((role, request_id))
        if role == "reviewer":
            return {"market_hypothesis": "fixture", "active_rules": ["fixture only"], "failure_modes": [], "change_reason": "initial", "evidence_ids": []}
        return {"intent": "set_target" if self.target != "hold" else "hold", "target_multiple": self.target if self.target != "hold" else None,
                "stop_price": "49000" if self.target != "0" else None, "reason": "fixture"}


def run_fixture(cfg, store):
    if store.read("runtime")[0] is not None:
        raise ValueError("fixture requires fresh database")
    clock = [1_800_000_000.0]
    market, policy = FixtureMarket(lambda: clock[0]), FixturePolicy()
    runner = Runner(cfg, store, market, policy, news=lambda *a, **k: [], clock=lambda: clock[0])
    results = []
    for i, target in enumerate(("hold", "1", "hold", "2", ".5", "0")):
        clock[0] = 1_800_000_000.0 + 300 * i
        policy.target = target
        results.append(runner.run(clock[0]))
        before = deepcopy(store.read("runtime")[0]["accounts"])
        assert runner.run(clock[0])["status"] == "duplicate"
        assert before == store.read("runtime")[0]["accounts"]
    state, _ = store.read("runtime")
    assert dec(state["accounts"]["llm"]["quantity"]) == 0
    return {"status": "fixture_complete", "cycles": results, "calls": policy.calls, "accounts": state["accounts"],
            "profitability_evidence": False}
