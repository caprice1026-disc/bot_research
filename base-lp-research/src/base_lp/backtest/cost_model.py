from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockFee:
    gas_price_wei: int
    gas_used: int
    l1_fee_wei: int = 0
    eth_price_usd: float = 2_000.0


@dataclass(frozen=True)
class CostBreakdown:
    l2_execution_usd: float
    l1_security_usd: float
    inventory_swap_fee_usd: float
    price_impact_usd: float
    slippage_mev_proxy_usd: float

    @property
    def total_usd(self) -> float:
        return sum(self.components())

    def components(self) -> tuple[float, ...]:
        return (
            self.l2_execution_usd,
            self.l1_security_usd,
            self.inventory_swap_fee_usd,
            self.price_impact_usd,
            self.slippage_mev_proxy_usd,
        )


class CostModel:
    def __init__(
        self,
        fee_tier: int,
        include_inventory_swap_fee: bool = True,
        include_price_impact: bool = True,
        include_slippage_mev_proxy: bool = True,
    ) -> None:
        self.fee_tier = fee_tier
        self.include_inventory_swap_fee = include_inventory_swap_fee
        self.include_price_impact = include_price_impact
        self.include_slippage_mev_proxy = include_slippage_mev_proxy

    def estimate(
        self,
        action: str,
        block_fee: BlockFee,
        token_price_usd: float,
        inventory_swap_usd: float,
    ) -> CostBreakdown:
        if action != "reset":
            return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
        l2 = block_fee.gas_price_wei * block_fee.gas_used * block_fee.eth_price_usd / 10**18
        l1 = block_fee.l1_fee_wei * block_fee.eth_price_usd / 10**18
        swap_fee = inventory_swap_usd * self.fee_tier / 1_000_000 if self.include_inventory_swap_fee else 0.0
        impact = inventory_swap_usd * 0.0005 if self.include_price_impact else 0.0
        mev_proxy = inventory_swap_usd * 0.0002 if self.include_slippage_mev_proxy else 0.0
        return CostBreakdown(l2, l1, swap_fee, impact, mev_proxy)
