from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from btc5m.events import parse_source_timestamp


@dataclass(frozen=True, slots=True)
class LeadLagResult:
    status: str
    reason: str | None
    event_count: int
    observations_by_horizon: dict[int, int]
    mean_signed_response_by_horizon: dict[int, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_count": self.event_count,
            "observations_by_horizon": self.observations_by_horizon,
            "mean_signed_response_by_horizon": self.mean_signed_response_by_horizon,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Receive-time lead-lag report",
            "",
            f"- result_state: `{self.status}`",
            f"- reason: `{self.reason or 'none'}`",
            f"- external_shocks: {self.event_count}",
            "",
            "| Horizon (ms) | Observations | Mean signed response |",
            "| ---: | ---: | ---: |",
        ]
        lines.extend(
            f"| {horizon} | {self.observations_by_horizon[horizon]} | "
            f"{self.mean_signed_response_by_horizon[horizon]:.8f} |"
            for horizon in sorted(self.observations_by_horizon)
        )
        lines.extend(
            [
                "",
                "This report is receive-time safe for the observed rows. It is not a "
                "profitability or trading result.",
            ]
        )
        return "\n".join(lines) + "\n"


def _float_value(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _receive_us(row: Mapping[str, object]) -> int | None:
    value = row.get("local_receive_ts")
    if value in (None, ""):
        return None
    return parse_source_timestamp(value)


def _row_price(row: Mapping[str, object]) -> float | None:
    price = _float_value(row.get("price"))
    if price is not None:
        return price
    bid = _float_value(row.get("bid"))
    ask = _float_value(row.get("ask"))
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _series(rows: Iterable[Mapping[str, object]]) -> list[tuple[int, float]]:
    output: list[tuple[int, float]] = []
    for row in rows:
        timestamp = _receive_us(row)
        price = _row_price(row)
        if timestamp is not None and price is not None and price > 0:
            output.append((timestamp, price))
    return sorted(output)


def event_study(
    external_rows: Iterable[Mapping[str, object]],
    polymarket_rows: Iterable[Mapping[str, object]],
    *,
    shock_return: float = 0.001,
    lookback_ms: int = 500,
    horizons_ms: Sequence[int] = (100, 250, 500, 1_000, 2_000, 5_000),
) -> LeadLagResult:
    """Estimate the response after large external moves using receive timestamps.

    The external shock is measured against the latest observation at least
    ``lookback_ms`` earlier.  All joins are backward/as-of: rows arriving after
    the decision time cannot be used to define the pre-event market price.
    """
    external = _series(external_rows)
    market = _series(polymarket_rows)
    horizons = tuple(sorted({int(horizon) for horizon in horizons_ms if horizon >= 0}))
    empty_counts = {horizon: 0 for horizon in horizons}
    empty_means = {horizon: 0.0 for horizon in horizons}
    if not external or not market:
        return LeadLagResult(
            status="insufficient_data",
            reason="missing_receive_time"
            if not external or not market
            else "empty_series",
            event_count=0,
            observations_by_horizon=empty_counts,
            mean_signed_response_by_horizon=empty_means,
        )

    market_times = [timestamp for timestamp, _ in market]
    market_prices = [price for _, price in market]
    external_times = [timestamp for timestamp, _ in external]
    event_times: list[tuple[int, float]] = []
    lookback_us = lookback_ms * 1_000
    for index, (timestamp, price) in enumerate(external):
        previous_index = (
            bisect_right(external_times, timestamp - lookback_us, 0, index) - 1
        )
        if previous_index < 0:
            continue
        previous_price = external[previous_index][1]
        move = price / previous_price - 1.0
        if abs(move) >= shock_return:
            event_times.append((timestamp, 1.0 if move > 0 else -1.0))

    sums = {horizon: 0.0 for horizon in horizons}
    counts = {horizon: 0 for horizon in horizons}
    for event_time, direction in event_times:
        pre_index = bisect_right(market_times, event_time) - 1
        if pre_index < 0:
            continue
        pre_price = market_prices[pre_index]
        for horizon in horizons:
            post_index = bisect_right(market_times, event_time + horizon * 1_000) - 1
            if post_index <= pre_index:
                continue
            response = (market_prices[post_index] - pre_price) * direction
            sums[horizon] += response
            counts[horizon] += 1

    means = {
        horizon: (sums[horizon] / counts[horizon] if counts[horizon] else 0.0)
        for horizon in horizons
    }
    if not event_times:
        return LeadLagResult(
            status="insufficient_data",
            reason="no_external_shocks",
            event_count=0,
            observations_by_horizon=counts,
            mean_signed_response_by_horizon=means,
        )
    return LeadLagResult(
        status="exploratory" if any(counts.values()) else "insufficient_data",
        reason=None if any(counts.values()) else "no_overlapping_market_response",
        event_count=len(event_times),
        observations_by_horizon=counts,
        mean_signed_response_by_horizon=means,
    )
