from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import polars as pl

from btc5m.collectors.polymarket import (
    MarketIdentity,
    market_identity_from_mapping,
)
from btc5m.coverage import CoverageTracker, coverage_channel, gap_overlaps
from btc5m.events import parse_source_timestamp
from btc5m.io import write_json
from btc5m.market_master import write_market_master
from btc5m.storage import (
    append_jsonl_rows,
    iter_jsonl,
    partition_path,
)

DEFAULT_REQUIRED_CHANNELS = (
    ("binance", "book_ticker", "BTCUSDT"),
    ("coinbase", "ticker", "BTC-USD"),
    ("hyperliquid", "bbo", "BTC"),
    ("chainlink", "chainlink_twap_60", "btc/usd"),
)


def _market_identity(row: Mapping[str, object]) -> MarketIdentity | None:
    return market_identity_from_mapping(row)


def select_five_minute_markets(
    market_rows: Iterable[Mapping[str, object]],
) -> tuple[list[MarketIdentity], list[dict[str, object]]]:
    selected: list[MarketIdentity] = []
    excluded: list[dict[str, object]] = []
    for row in market_rows:
        identity = _market_identity(row)
        if identity is None:
            excluded.append(dict(row))
        else:
            selected.append(identity)
    selected.sort(key=lambda item: (item.window_start_ts, item.condition_id))
    excluded.sort(key=lambda row: str(row.get("condition_id") or row.get("slug") or ""))
    return selected, excluded


def _channel_key(row: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(row.get("source") or ""),
        coverage_channel(row),
        str(row.get("symbol") or ""),
        str(row.get("market_id") or ""),
    )


def _ts(row: Mapping[str, object]) -> int | None:
    return parse_source_timestamp(row.get("local_receive_ts"))


ChannelStats = dict[tuple[str, str, str, str], tuple[int, int, int]]


def _update_channel_stats(
    stats: ChannelStats,
    row: Mapping[str, object],
    timestamp: int,
) -> None:
    key = _channel_key(row)
    count, first, last = stats.get(key, (0, timestamp, timestamp))
    stats[key] = (count + 1, min(first, timestamp), max(last, timestamp))


def _channel_coverage(
    stats: ChannelStats,
    gaps: Sequence[Mapping[str, object]],
    *,
    source: str,
    channel: str,
    symbol: str,
    market_id: str = "",
    start_ts: int,
    end_ts: int,
) -> dict[str, object]:
    count, first, last = stats.get((source, channel, symbol, market_id), (0, 0, 0))
    matching_gaps = [
        gap
        for gap in gaps
        if gap_overlaps(
            gap,
            start_ts=start_ts,
            end_ts=end_ts,
            source=source,
            channel=channel,
            symbol=symbol,
            market_id=market_id,
        )
    ]
    reasons: list[str] = []
    if count == 0:
        reasons.append("missing_required_channel")
    elif first > start_ts or last < end_ts:
        reasons.append("insufficient_channel_window")
    if matching_gaps:
        reasons.append("receive_gap")
    return {
        "source": source,
        "channel": channel,
        "symbol": symbol,
        "market_id": market_id,
        "first_ts": first if count else None,
        "last_ts": last if count else None,
        "row_count": count,
        "gap_count": len(matching_gaps),
        "continuous": count > 0 and not reasons,
        "reasons": sorted(set(reasons)),
    }


def _build_report_from_stats(
    markets: list[MarketIdentity],
    excluded: list[dict[str, object]],
    stats: ChannelStats,
    gaps: list[dict[str, object]],
    *,
    event_row_count: int,
    inversion_count: int,
    lookback_ms: int = 500,
    horizons_ms: Iterable[int] = (100, 250, 500, 1_000, 2_000, 5_000),
    chainlink_history_seconds: int = 60,
) -> dict[str, object]:
    max_horizon_ms = max((int(value) for value in horizons_ms if int(value) >= 0), default=0)
    history_us = max(int(lookback_ms) * 1_000, int(chainlink_history_seconds) * 1_000_000)
    required = list(DEFAULT_REQUIRED_CHANNELS)
    market_reports: list[dict[str, object]] = []
    for identity in markets:
        analysis_start = identity.window_start_ts - history_us
        analysis_end = identity.window_end_ts + max_horizon_ms * 1_000
        channels = [
            _channel_coverage(
                stats,
                gaps,
                source=source,
                channel=channel,
                symbol=symbol,
                start_ts=analysis_start,
                end_ts=analysis_end,
            )
            for source, channel, symbol in required
        ]
        for token in (identity.up_token_id, identity.down_token_id):
            channels.append(
                _channel_coverage(
                    stats,
                    gaps,
                    source="polymarket",
                    channel="quote",
                    symbol=token,
                    market_id=identity.condition_id,
                    start_ts=identity.window_start_ts,
                    end_ts=analysis_end,
                )
            )
        reasons: set[str] = set()
        for channel in channels:
            channel_reasons = channel.get("reasons")
            if isinstance(channel_reasons, list):
                reasons.update(str(reason) for reason in channel_reasons)
        sorted_reasons = sorted(reasons)
        market_reports.append(
            {
                "slug": identity.slug,
                "condition_id": identity.condition_id,
                "up_token_id": identity.up_token_id,
                "down_token_id": identity.down_token_id,
                "window_start_ts": identity.window_start_ts,
                "window_end_ts": identity.window_end_ts,
                "analysis_start_ts": analysis_start,
                "analysis_end_ts": analysis_end,
                "eligible": not sorted_reasons,
                "reasons": sorted_reasons,
                "channels": channels,
            }
        )
    return {
        "status": "ready"
        if any(row["eligible"] for row in market_reports)
        else "insufficient_data",
        "selected_market_ids": [identity.condition_id for identity in markets],
        "excluded_market_ids": [
            str(row.get("condition_id") or row.get("slug") or "") for row in excluded
        ],
        "excluded_markets": [
            {
                "slug": str(row.get("slug") or ""),
                "condition_id": str(row.get("condition_id") or ""),
            }
            for row in excluded
        ],
        "selected_market_count": len(markets),
        "excluded_market_count": len(excluded),
        "event_row_count": event_row_count,
        "gap_count": len(gaps),
        "inversion_count": inversion_count,
        "gaps": gaps,
        "markets": market_reports,
    }


def build_selection_report(
    market_rows: Iterable[Mapping[str, object]],
    event_rows: Iterable[Mapping[str, object]],
    *,
    lookback_ms: int = 500,
    horizons_ms: Iterable[int] = (100, 250, 500, 1_000, 2_000, 5_000),
    chainlink_history_seconds: int = 60,
    max_gap_seconds: float = 5.0,
) -> dict[str, object]:
    markets, excluded = select_five_minute_markets(market_rows)
    stats: ChannelStats = {}
    tracker = CoverageTracker(max_gap_seconds=max_gap_seconds)
    event_count = 0
    for row in event_rows:
        event_count += 1
        timestamp = _ts(row)
        if timestamp is None:
            continue
        _update_channel_stats(stats, row, timestamp)
        tracker.observe(row, connection_id=str(row.get("connection_id") or "") or None)
    return _build_report_from_stats(
        markets,
        excluded,
        stats,
        tracker.to_dicts(),
        event_row_count=event_count,
        inversion_count=tracker.inversion_count,
        lookback_ms=lookback_ms,
        horizons_ms=horizons_ms,
        chainlink_history_seconds=chainlink_history_seconds,
    )


def _datetime_from_us(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)


def select_run(
    input_root: Path,
    output_root: Path,
    *,
    lookback_ms: int = 500,
    horizons_ms: tuple[int, ...] = (100, 250, 500, 1_000, 2_000, 5_000),
    chainlink_history_seconds: int = 60,
    max_gap_seconds: float = 5.0,
    copy_events: bool = False,
) -> dict[str, object]:
    """Create a selection manifest while leaving the raw run untouched.

    The default is an index/manifest operation.  ``copy_events`` is explicit
    because duplicating a multi-gigabyte raw run is an operational decision.
    """

    if copy_events and (output_root / "raw").exists():
        raise FileExistsError(
            f"selected raw output already exists: {output_root / 'raw'}"
        )
    master_path = input_root / "market_master" / "markets.parquet"
    market_rows = pl.read_parquet(master_path).to_dicts() if master_path.exists() else []
    markets, excluded = select_five_minute_markets(market_rows)
    selected_ids = {identity.condition_id for identity in markets}
    selected_tokens = {identity.up_token_id for identity in markets} | {
        identity.down_token_id for identity in markets
    }
    if not markets:
        report = build_selection_report(
            [],
            [],
            lookback_ms=lookback_ms,
            horizons_ms=horizons_ms,
            chainlink_history_seconds=chainlink_history_seconds,
            max_gap_seconds=max_gap_seconds,
        )
        report.update(
            {
                "input_root": str(input_root),
                "output_root": str(output_root),
                "raw_preserved": True,
                "copied_events": False,
            }
        )
        write_json(output_root / "selection_manifest.json", report)
        return report

    history_us = max(lookback_ms * 1_000, chainlink_history_seconds * 1_000_000)
    min_start = min(identity.window_start_ts for identity in markets) - history_us
    max_end = max(identity.window_end_ts for identity in markets) + max(horizons_ms) * 1_000
    tracker = CoverageTracker(max_gap_seconds=max_gap_seconds)
    counts: dict[str, int] = defaultdict(int)
    selected_event_files: set[Path] = set()
    copy_batches: dict[Path, list[dict[str, object]]] = defaultdict(list)
    stats: ChannelStats = {}
    event_row_count = 0
    # Only the current raw files are walked; logs and manifests are not events.
    for path in sorted(input_root.rglob("events.jsonl")):
        for row in iter_jsonl(path):
            source = str(row.get("source") or "")
            timestamp = parse_source_timestamp(row.get("local_receive_ts"))
            in_support = timestamp is not None and min_start <= timestamp <= max_end
            is_selected = False
            if source == "polymarket":
                is_selected = (
                    str(row.get("market_id") or "") in selected_ids
                    and str(row.get("symbol") or "") in selected_tokens
                )
            elif source in {"chainlink", "binance", "coinbase", "hyperliquid"}:
                is_selected = in_support
            if not is_selected:
                continue
            selected_event_files.add(path)
            counts[source] += 1
            event_row_count += 1
            if timestamp is not None:
                _update_channel_stats(stats, row, timestamp)
            tracker.observe(row, connection_id=str(row.get("connection_id") or "") or None)
            if copy_events and timestamp is not None:
                output_path = partition_path(output_root / "raw", source, _datetime_from_us(timestamp))
                copy_batches[output_path].append(row)
                if len(copy_batches[output_path]) >= 1_000:
                    append_jsonl_rows(output_path, copy_batches[output_path])
                    copy_batches[output_path].clear()
    for path, rows in copy_batches.items():
        if rows:
            append_jsonl_rows(path, rows)

    report = _build_report_from_stats(
        markets,
        excluded,
        stats,
        tracker.to_dicts(),
        event_row_count=event_row_count,
        inversion_count=tracker.inversion_count,
        lookback_ms=lookback_ms,
        horizons_ms=horizons_ms,
        chainlink_history_seconds=chainlink_history_seconds,
    )
    if copy_events:
        write_market_master(output_root / "market_master" / "markets.parquet", markets)
    report.update(
        {
            "input_root": str(input_root),
            "output_root": str(output_root),
            "raw_preserved": True,
            "copied_events": copy_events or (output_root / "raw").exists(),
            "selected_event_files": [str(path) for path in sorted(selected_event_files)],
            "selected_event_counts": dict(sorted(counts.items())),
            "support_start_ts": min_start,
            "support_end_ts": max_end,
        }
    )
    write_json(output_root / "selection_manifest.json", report)
    return report
