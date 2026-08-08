from __future__ import annotations

from dataclasses import dataclass

from base_lp.schemas import SwapEvent


@dataclass
class PoolState:
    tick: int | None = None
    sqrt_price_x96: int | None = None
    liquidity: int | None = None
    last_key: tuple[int, int, int] | None = None

    def replay_swap(self, event: SwapEvent) -> None:
        if self.last_key is not None and event.stable_key <= self.last_key:
            raise ValueError(f"swap event is not strictly after prior event: {event.stable_key}")
        self.tick = event.tick
        self.sqrt_price_x96 = event.sqrt_price_x96
        self.liquidity = event.liquidity
        self.last_key = event.stable_key
