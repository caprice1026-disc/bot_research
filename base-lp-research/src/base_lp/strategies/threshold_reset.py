from __future__ import annotations

from base_lp.backtest.models import Decision, Observation


class ThresholdResetStrategy:
    name = "threshold_reset"

    def __init__(
        self,
        half_width_ticks: int,
        trigger_ratio: float,
        cooldown_seconds: int = 0,
        safety_margin_usd: float = 0.0,
    ) -> None:
        if half_width_ticks <= 0 or not 0 < trigger_ratio <= 1:
            raise ValueError("invalid threshold reset parameters")
        self.half_width_ticks = half_width_ticks
        self.trigger_ratio = trigger_ratio
        self.cooldown_seconds = cooldown_seconds
        self.safety_margin_usd = safety_margin_usd

    def initial_range(self, center_tick: int) -> tuple[int, int]:
        return center_tick - self.half_width_ticks, center_tick + self.half_width_ticks

    def decide(self, observation: Observation) -> Decision:
        center = (observation.current_lower_tick + observation.current_upper_tick) // 2
        distance = abs(observation.tick - center)
        ratio = distance / self.half_width_ticks
        elapsed = observation.timestamp - observation.last_reset_timestamp
        expected_benefit = observation.capital_usd * 0.0005 * max(ratio, 0.0)
        if elapsed < self.cooldown_seconds:
            return Decision(
                "hold",
                observation.current_lower_tick,
                observation.current_upper_tick,
                "cooldown gate",
                expected_benefit,
            )
        if ratio < self.trigger_ratio:
            return Decision(
                "hold",
                observation.current_lower_tick,
                observation.current_upper_tick,
                "below threshold",
                expected_benefit,
            )
        if expected_benefit <= observation.estimated_rebalance_cost_usd + self.safety_margin_usd:
            return Decision(
                "hold",
                observation.current_lower_tick,
                observation.current_upper_tick,
                "cost gate: expected benefit does not exceed cost",
                expected_benefit,
            )
        return Decision(
            "reset",
            observation.tick - self.half_width_ticks,
            observation.tick + self.half_width_ticks,
            "threshold reached; reset range",
            expected_benefit,
        )
