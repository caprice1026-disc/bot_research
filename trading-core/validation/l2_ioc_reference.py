"""Single-use, single-time L2 IOC reference; not an exchange or maker simulator.

A copied snapshot is an independent scenario, NEVER shared available liquidity.
No fees, tick/lot normalization, hidden depth, latency or queue are inferred.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import math

ZERO = Decimal("0")

def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("nonfinite price or size")
    return result

@dataclass(frozen=True)
class Fill:
    price: Decimal
    quantity: Decimal

@dataclass(frozen=True)
class IOCResult:
    side: str
    requested: Decimal
    fills: tuple[Fill, ...]
    filled: Decimal
    canceled: Decimal
    notional: Decimal
    average_price: Decimal | None

@dataclass(frozen=True)
class SingleUseL2:
    """Frozen observed depth consumed by exactly one IOC estimate."""
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    as_of: float
    _used: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self):
        if not math.isfinite(self.as_of):
            raise ValueError("invalid snapshot time")
        def levels(raw, reverse):
            parsed = tuple((decimal(p), decimal(q)) for p, q in raw)
            if any(p <= 0 or q < 0 for p, q in parsed):
                raise ValueError("nonpositive price or negative depth")
            if len({p for p, _ in parsed}) != len(parsed):
                raise ValueError("duplicate price levels")
            return tuple(sorted(((p, q) for p, q in parsed if q > 0), reverse=reverse))
        object.__setattr__(self, "bids", levels(self.bids, True))
        object.__setattr__(self, "asks", levels(self.asks, False))
        if self.bids and self.asks and self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed or locked book")

    @classmethod
    def from_book(cls, book, *, as_of: float, max_quote_age: float):
        """Copy an existing btc_data.Book only after validity/freshness checks.

        Freshness is receive-time only; source timestamps, clock quality and
        completeness of the event tape remain the caller's responsibility.
        """
        if not all(math.isfinite(t) for t in (as_of, max_quote_age)) or max_quote_age < 0:
            raise ValueError("invalid freshness constraint")
        if not book.valid or book.quote_ts is None:
            raise ValueError("book is invalid or unobserved")
        age = as_of - book.quote_ts
        if not math.isfinite(age) or not 0 <= age <= max_quote_age:
            raise ValueError("book is future or stale")
        return cls(tuple(book.bids.items()), tuple(book.asks.items()), as_of)

    def ioc(self, *, side: str, quantity, limit=None):
        if self._used:
            raise ValueError("snapshot already consumed; do not reuse liquidity")
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        requested = decimal(quantity)
        cap = None if limit is None else decimal(limit)
        if requested <= 0 or (cap is not None and cap <= 0):
            raise ValueError("quantity and limit must be positive")
        object.__setattr__(self, "_used", True)
        remaining, fills = requested, []
        for price, available in (self.asks if side == "buy" else self.bids):
            if cap is not None and ((side == "buy" and price > cap) or
                                    (side == "sell" and price < cap)):
                break
            taken = min(remaining, available)
            if taken:
                fills.append(Fill(price, taken))
                remaining -= taken
            if not remaining:
                break
        filled = requested - remaining
        notional = sum((f.price * f.quantity for f in fills), ZERO)
        return IOCResult(side, requested, tuple(fills), filled, remaining,
                         notional, notional / filled if filled else None)
