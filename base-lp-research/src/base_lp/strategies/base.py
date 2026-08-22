from __future__ import annotations

from typing import Protocol

from base_lp.backtest.models import Decision, Observation


class Strategy(Protocol):
    name: str

    def initial_range(self, center_tick: int) -> tuple[int, int]: ...

    def decide(self, observation: Observation) -> Decision: ...
