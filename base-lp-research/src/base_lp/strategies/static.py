from __future__ import annotations

from base_lp.backtest.models import Decision, Observation


class StaticRangeStrategy:
    name = "static_narrow"

    def __init__(self, half_width_ticks: int) -> None:
        if half_width_ticks <= 0:
            raise ValueError("half_width_ticks must be positive")
        self.half_width_ticks = half_width_ticks

    def initial_range(self, center_tick: int) -> tuple[int, int]:
        return center_tick - self.half_width_ticks, center_tick + self.half_width_ticks

    def decide(self, observation: Observation) -> Decision:
        return Decision(
            action="hold",
            target_lower_tick=observation.current_lower_tick,
            target_upper_tick=observation.current_upper_tick,
            reason="static range",
        )
