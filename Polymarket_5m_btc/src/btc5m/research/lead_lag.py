from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from btc5m.coverage import gap_overlaps
from btc5m.events import parse_source_timestamp


@dataclass(frozen=True, slots=True)
class SeriesLeadLagResult:
    observations_by_horizon: dict[int, int]
    mean_signed_response_by_horizon: dict[int, float | None]
    gap_excluded_by_horizon: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "observations_by_horizon": self.observations_by_horizon,
            "mean_signed_response_by_horizon": self.mean_signed_response_by_horizon,
            "gap_excluded_by_horizon": self.gap_excluded_by_horizon,
        }


@dataclass(frozen=True, slots=True)
class ShockEvent:
    timestamp_us: int
    direction: float
    source: str
    price_basis: str
    move: float
    baseline_age_us: int


@dataclass(frozen=True, slots=True)
class LeadLagResult:
    status: str
    reason: str | None
    event_count: int
    observations_by_horizon: dict[int, int]
    mean_signed_response_by_horizon: dict[int, float | None]
    series_results: dict[str, SeriesLeadLagResult]
    shock_provenance: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_count": self.event_count,
            "observations_by_horizon": self.observations_by_horizon,
            "mean_signed_response_by_horizon": self.mean_signed_response_by_horizon,
            "series_results": {
                label: result.to_dict() for label, result in self.series_results.items()
            },
            "shock_provenance": self.shock_provenance,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Receive-time lead-lag report",
            "",
            f"- result_state: `{self.status}`",
            f"- reason: `{self.reason or 'none'}`",
            f"- external_shocks: {self.event_count}",
            f"- shock_provenance: {self.shock_provenance or 'none'}",
            "",
            "| Horizon (ms) | Observations | Mean signed response |",
            "| ---: | ---: | ---: |",
        ]
        lines.extend(
            f"| {horizon} | {self.observations_by_horizon[horizon]} | "
            f"{('n/a' if self.mean_signed_response_by_horizon[horizon] is None else f'{self.mean_signed_response_by_horizon[horizon]:.8f}')} |"
            for horizon in sorted(self.observations_by_horizon)
        )
        if self.series_results:
            lines.extend(["", "## Isolated market series", ""])
            for label, result in sorted(self.series_results.items()):
                lines.extend([f"### `{label}`", ""])
                lines.extend(
                    f"| {horizon} | {result.observations_by_horizon[horizon]} | "
                    f"{('n/a' if result.mean_signed_response_by_horizon[horizon] is None else f'{result.mean_signed_response_by_horizon[horizon]:.8f}')} |"
                    for horizon in sorted(result.observations_by_horizon)
                )
        lines.extend(
            [
                "",
                "Responses are isolated by market, token, and price basis. This is not "
                "a profitability or trading result.",
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
    event_type = str(row.get("event_type") or "").lower()
    bid = _float_value(row.get("bid"))
    ask = _float_value(row.get("ask"))
    quote_events = {
        "book",
        "best_bid_ask",
        "book_ticker",
        "bbo",
        "ticker",
        "price_change",
    }
    trade_events = {"agg_trade", "trade", "last_trade_price", "market_trade"}
    if event_type in quote_events:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0
    price = _float_value(row.get("price"))
    if event_type in trade_events:
        return price
    if price is not None:
        return price
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _price_basis(row: Mapping[str, object]) -> str:
    event_type = str(row.get("event_type") or "").lower()
    if event_type in {
        "book",
        "best_bid_ask",
        "book_ticker",
        "bbo",
        "ticker",
        "price_change",
    }:
        return "quote_mid"
    if event_type in {"agg_trade", "trade", "last_trade_price", "market_trade"}:
        return "trade"
    return "price"


def _raw_mapping(row: Mapping[str, object]) -> Mapping[str, object]:
    payload = row.get("raw_payload")
    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, str) and payload:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def _series_identity(row: Mapping[str, object]) -> tuple[str, str]:
    raw = _raw_mapping(row)
    change = raw.get("price_change")
    change_mapping = change if isinstance(change, Mapping) else {}
    market_id = (
        row.get("market_id")
        or row.get("condition_id")
        or row.get("market")
        or raw.get("market")
        or raw.get("condition_id")
    )
    symbol = (
        row.get("symbol")
        or row.get("token_id")
        or row.get("asset_id")
        or change_mapping.get("asset_id")
        or raw.get("asset_id")
    )
    return str(market_id or ""), str(symbol or "")


def _series(
    rows: Iterable[Mapping[str, object]],
    *,
    include_source: bool,
) -> dict[tuple[str, str, str, str], list[tuple[int, float]]]:
    grouped: dict[tuple[str, str, str, str], list[tuple[int, float, int]]] = (
        defaultdict(list)
    )
    for order, row in enumerate(rows):
        timestamp = _receive_us(row)
        price = _row_price(row)
        if timestamp is None or price is None or price < 0:
            continue
        market_id, symbol = _series_identity(row)
        source = str(row.get("source") or "") if include_source else ""
        key = (source, market_id, symbol, _price_basis(row))
        grouped[key].append((timestamp, price, order))
    return {
        key: [
            (timestamp, price)
            for timestamp, price, _ in sorted(
                values, key=lambda item: (item[0], item[2])
            )
        ]
        for key, values in grouped.items()
    }


def _series_label(key: tuple[str, str, str, str]) -> str:
    _, market_id, symbol, basis = key
    return "/".join((market_id or "unknown-market", symbol or "unknown-token", basis))


def _gap_channels(source: str, basis: str) -> set[str]:
    if basis == "trade":
        return {"trade", "agg_trade", "market_trade"}
    if basis == "quote_mid":
        return {
            "quote",
            "book",
            "best_bid_ask",
            "book_ticker",
            "bbo",
            "ticker",
            "price_change",
        }
    return {basis, "price"}


def _series_gap_overlaps(
    gaps: Sequence[Mapping[str, object]],
    *,
    start_ts: int,
    end_ts: int,
    source: str,
    market_id: str,
    symbol: str,
    basis: str,
) -> bool:
    channels = _gap_channels(source, basis)
    return any(
        gap_overlaps(
            gap,
            start_ts=start_ts,
            end_ts=end_ts,
            source=source,
            symbol=symbol,
            market_id=market_id,
        )
        and str(gap.get("channel") or "") in channels
        for gap in gaps
    )


def _measure_series(
    event_times: Sequence[ShockEvent],
    series: Sequence[tuple[int, float]],
    horizons: Sequence[int],
    *,
    gaps: Sequence[Mapping[str, object]],
    source: str,
    market_id: str,
    symbol: str,
    basis: str,
) -> SeriesLeadLagResult:
    sums = {horizon: 0.0 for horizon in horizons}
    counts = {horizon: 0 for horizon in horizons}
    gap_excluded = {horizon: 0 for horizon in horizons}
    market_times = [timestamp for timestamp, _ in series]
    market_prices = [price for _, price in series]
    for shock in event_times:
        event_time = shock.timestamp_us
        direction = shock.direction
        pre_index = bisect_right(market_times, event_time) - 1
        if pre_index < 0:
            continue
        pre_price = market_prices[pre_index]
        for horizon in horizons:
            deadline = event_time + horizon * 1_000
            if market_times[-1] < deadline:
                continue
            if _series_gap_overlaps(
                gaps,
                start_ts=event_time,
                end_ts=deadline,
                source=source,
                market_id=market_id,
                symbol=symbol,
                basis=basis,
            ):
                gap_excluded[horizon] += 1
                continue
            post_index = bisect_right(market_times, deadline) - 1
            if post_index < pre_index:
                continue
            sums[horizon] += (market_prices[post_index] - pre_price) * direction
            counts[horizon] += 1
    return SeriesLeadLagResult(
        observations_by_horizon=counts,
        mean_signed_response_by_horizon={
            horizon: (sums[horizon] / counts[horizon] if counts[horizon] else None)
            for horizon in horizons
        },
        gap_excluded_by_horizon=gap_excluded,
    )


def _deduplicate_shocks(
    candidates: Iterable[ShockEvent],
    *,
    cooldown_ms: int,
) -> list[ShockEvent]:
    accepted: list[ShockEvent] = []
    cooldown_us = max(0, cooldown_ms) * 1_000
    for candidate in sorted(candidates, key=lambda item: (item.timestamp_us, -abs(item.move))):
        if accepted and candidate.timestamp_us - accepted[-1].timestamp_us <= cooldown_us:
            if abs(candidate.move) > abs(accepted[-1].move):
                accepted[-1] = candidate
            continue
        accepted.append(candidate)
    return accepted


def event_study(
    external_rows: Iterable[Mapping[str, object]],
    polymarket_rows: Iterable[Mapping[str, object]],
    *,
    shock_return: float = 0.001,
    lookback_ms: int = 500,
    horizons_ms: Sequence[int] = (100, 250, 500, 1_000, 2_000, 5_000),
    max_shock_age_ms: int = 1_000,
    shock_cooldown_ms: int = 250,
    gap_intervals: Sequence[Mapping[str, object]] = (),
) -> LeadLagResult:
    """Estimate the response after large external moves using receive timestamps.

    The external shock is measured against the latest observation at least
    ``lookback_ms`` earlier.  All joins are backward/as-of: rows arriving after
    the decision time cannot be used to define the pre-event market price.
    """
    external_input = list(external_rows)
    market_input = list(polymarket_rows)
    external = _series(external_input, include_source=True)
    market = _series(market_input, include_source=False)
    horizons = tuple(sorted({int(horizon) for horizon in horizons_ms if horizon >= 0}))
    empty_counts = {horizon: 0 for horizon in horizons}
    empty_means: dict[int, float | None] = {horizon: None for horizon in horizons}
    if not external or not market:
        return LeadLagResult(
            status="insufficient_data",
            reason=(
                "empty_series"
                if not external_input and not market_input
                else "missing_receive_time"
            ),
            event_count=0,
            observations_by_horizon=empty_counts,
            mean_signed_response_by_horizon=empty_means,
            series_results={},
            shock_provenance={},
        )

    shock_candidates: list[ShockEvent] = []
    lookback_us = lookback_ms * 1_000
    for (source, market_id, symbol, basis), series in external.items():
        external_times = [timestamp for timestamp, _ in series]
        for index, (timestamp, price) in enumerate(series):
            previous_index = (
                bisect_right(external_times, timestamp - lookback_us, 0, index) - 1
            )
            if previous_index < 0:
                continue
            baseline_age_us = timestamp - external_times[previous_index]
            if baseline_age_us > (lookback_ms + max_shock_age_ms) * 1_000:
                continue
            previous_price = series[previous_index][1]
            if previous_price <= 0:
                continue
            move = price / previous_price - 1.0
            if abs(move) >= shock_return:
                if _series_gap_overlaps(
                    gap_intervals,
                    start_ts=external_times[previous_index],
                    end_ts=timestamp,
                    source=source,
                    market_id=market_id,
                    symbol=symbol,
                    basis=basis,
                ):
                    continue
                shock_candidates.append(
                    ShockEvent(
                        timestamp_us=timestamp,
                        direction=1.0 if move > 0 else -1.0,
                        source=source,
                        price_basis=basis,
                        move=move,
                        baseline_age_us=baseline_age_us,
                    )
                )
    event_times = _deduplicate_shocks(
        shock_candidates,
        cooldown_ms=shock_cooldown_ms,
    )

    series_results = {
        _series_label(key): _measure_series(
            event_times,
            series,
            horizons,
            gaps=gap_intervals,
            source="polymarket",
            market_id=key[1],
            symbol=key[2],
            basis=key[3],
        )
        for key, series in market.items()
    }
    aggregate = (
        next(iter(series_results.values())) if len(series_results) == 1 else None
    )
    counts = (
        aggregate.observations_by_horizon if aggregate is not None else empty_counts
    )
    means = (
        aggregate.mean_signed_response_by_horizon
        if aggregate is not None
        else empty_means
    )
    if not event_times:
        return LeadLagResult(
            status="insufficient_data",
            reason="no_external_shocks",
            event_count=0,
            observations_by_horizon=counts,
            mean_signed_response_by_horizon=means,
            series_results=series_results,
            shock_provenance={},
        )
    shock_provenance: dict[str, int] = defaultdict(int)
    for shock in event_times:
        shock_provenance[f"{shock.source}/{shock.price_basis}"] += 1
    return LeadLagResult(
        status=(
            "exploratory"
            if any(
                count
                for result in series_results.values()
                for count in result.observations_by_horizon.values()
            )
            else "insufficient_data"
        ),
        reason=(
            None
            if len(series_results) == 1 and any(counts.values())
            else "series_separated"
            if len(series_results) > 1
            else "no_overlapping_market_response"
        ),
        event_count=len(event_times),
        observations_by_horizon=counts,
        mean_signed_response_by_horizon=means,
        series_results=series_results,
        shock_provenance=dict(sorted(shock_provenance.items())),
    )
