from __future__ import annotations

import math


def half_width_ticks_for_pct(half_width_pct: float, tick_spacing: int) -> int:
    if half_width_pct <= 0 or tick_spacing <= 0:
        raise ValueError("half_width_pct and tick_spacing must be positive")
    raw_ticks = math.log1p(half_width_pct / 100.0) / math.log(1.0001)
    return math.ceil(raw_ticks / tick_spacing) * tick_spacing
