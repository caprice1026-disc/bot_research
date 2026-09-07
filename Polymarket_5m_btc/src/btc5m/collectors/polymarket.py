from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent

_WINDOW_RE = re.compile(r"^btc-updown-5m-(\d{10,13})$")


@dataclass(frozen=True, slots=True)
class MarketIdentity:
    slug: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    window_start_ts: int
    window_end_ts: int


def build_market_specs(token_ids: list[str] | tuple[str, ...]) -> tuple[object]:
    from polymarket.streams import MarketSpec

    if not token_ids:
        raise ValueError("token_ids must not be empty")
    return (MarketSpec(token_ids=token_ids, custom_feature_enabled=True),)


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _stable_payload_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _level(levels: object, *, best: str) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(levels, list):
        return None, None
    parsed = [
        (Decimal(str(item["price"])), Decimal(str(item["size"])))
        for item in levels
        if isinstance(item, Mapping) and item.get("price") not in (None, "")
    ]
    if not parsed:
        return None, None
    return (max if best == "bid" else min)(parsed, key=lambda item: item[0])


def parse_polymarket_message(
    payload: Mapping[str, Any],
    received: ReceiveStamp,
) -> list[RawEvent]:
    raw_payload = dict(payload)
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, Mapping):
        payload = {
            **dict(nested_payload),
            "event_type": payload.get("event_type") or payload.get("type"),
        }
    event_type = str(payload.get("event_type") or payload.get("type") or "")
    timestamp = payload.get("timestamp")
    asset_id = str(payload.get("asset_id") or "")
    market = str(payload.get("market") or "")
    if event_type == "book":
        bid, bid_size = _level(payload.get("bids"), best="bid")
        ask, ask_size = _level(payload.get("asks"), best="ask")
        sequence = (
            f"{timestamp}:{asset_id}:{payload['hash']}"
            if payload.get("hash")
            else f"{timestamp}:{asset_id}:{_stable_payload_id(payload)}"
        )
        return [
            RawEvent.from_message(
                source="polymarket",
                symbol=asset_id,
                event_type="book",
                payload=raw_payload,
                received=received,
                source_event_ts=timestamp,
                source_publish_ts=None,
                sequence_id=sequence,
                price=None,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
                market_id=market,
                receive_id=f"{received.local_monotonic_ns}:book:{asset_id}",
            )
        ]
    if event_type == "price_change":
        output: list[RawEvent] = []
        for change_index, change in enumerate(payload.get("price_changes", [])):
            if not isinstance(change, Mapping):
                continue
            change_asset_id = str(change.get("asset_id") or asset_id)
            change_sequence = (
                f"{timestamp}:{change_asset_id}:{change.get('hash')}:{change_index}"
                if change.get("hash")
                else f"{timestamp}:{change_asset_id}:{_stable_payload_id(change)}:{change_index}"
            )
            output.append(
                RawEvent.from_message(
                    source="polymarket",
                    symbol=change_asset_id,
                    event_type="price_change",
                    payload={**raw_payload, "price_change": dict(change)},
                    received=received,
                    source_event_ts=timestamp,
                    source_publish_ts=None,
                    sequence_id=change_sequence,
                    price=None,
                    bid=_decimal(change.get("best_bid")),
                    ask=_decimal(change.get("best_ask")),
                    bid_size=None,
                    ask_size=None,
                    market_id=str(change.get("market") or market),
                    receive_id=f"{received.local_monotonic_ns}:price_change:{change_asset_id}:{change_index}",
                )
            )
        return output
    if event_type == "last_trade_price":
        return [
            RawEvent.from_message(
                source="polymarket",
                symbol=asset_id,
                event_type="last_trade_price",
                payload=raw_payload,
                received=received,
                source_event_ts=timestamp,
                source_publish_ts=None,
                sequence_id=str(payload["transaction_hash"])
                if payload.get("transaction_hash")
                else None,
                price=_decimal(payload.get("price")),
                bid=None,
                ask=None,
                bid_size=None,
                ask_size=None,
                market_id=market,
                receive_id=f"{received.local_monotonic_ns}:last_trade:{asset_id}",
            )
        ]
    if event_type == "best_bid_ask":
        return [
            RawEvent.from_message(
                source="polymarket",
                symbol=asset_id,
                event_type="best_bid_ask",
                payload=raw_payload,
                received=received,
                source_event_ts=timestamp,
                source_publish_ts=None,
                sequence_id=f"{timestamp}:{asset_id}:{_stable_payload_id(payload)}",
                price=None,
                bid=_decimal(payload.get("best_bid")),
                ask=_decimal(payload.get("best_ask")),
                bid_size=None,
                ask_size=None,
                market_id=market,
                receive_id=f"{received.local_monotonic_ns}:best_bid_ask:{asset_id}",
            )
        ]
    if event_type in {"tick_size_change", "market_resolved", "new_market"}:
        return [
            RawEvent.from_message(
                source="polymarket",
                symbol=asset_id or market,
                event_type=event_type,
                payload=raw_payload,
                received=received,
                source_event_ts=timestamp,
                source_publish_ts=None,
                sequence_id=f"{timestamp}:{asset_id or market}:{event_type}:{_stable_payload_id(payload)}",
                price=None,
                bid=None,
                ask=None,
                bid_size=None,
                ask_size=None,
                market_id=market,
                receive_id=f"{received.local_monotonic_ns}:{event_type}:{asset_id or market}",
            )
        ]
    return []


def _window_from_slug(slug: str) -> tuple[int, int] | None:
    match = _WINDOW_RE.fullmatch(slug)
    if match is None:
        return None
    raw = int(match.group(1))
    start_seconds = raw / 1000 if len(match.group(1)) == 13 else raw
    start_us = int(start_seconds * 1_000_000)
    return start_us, start_us + 300_000_000


def market_identity_from_mapping(market: Mapping[str, Any]) -> MarketIdentity | None:
    slug = str(market.get("slug") or "")
    window = _window_from_slug(slug)
    if window is None:
        return None
    outcomes = market.get("outcomes")
    if isinstance(outcomes, Mapping):
        up = outcomes.get("yes") or outcomes.get("up")
        down = outcomes.get("no") or outcomes.get("down")
        if isinstance(up, Mapping):
            up_token = str(up.get("token_id") or up.get("tokenId") or "")
        else:
            up_token = str(up or "")
        if isinstance(down, Mapping):
            down_token = str(down.get("token_id") or down.get("tokenId") or "")
        else:
            down_token = str(down or "")
    else:
        up_token = str(market.get("up_token_id") or "")
        down_token = str(market.get("down_token_id") or "")
    condition = str(market.get("condition_id") or market.get("conditionId") or "")
    if not slug or not condition or not up_token or not down_token:
        return None
    return MarketIdentity(slug, condition, up_token, down_token, window[0], window[1])


async def discover_btc_5m_markets(
    client: Any,
    *,
    now: datetime | None = None,
) -> list[MarketIdentity]:
    # The official AsyncPublicClient returns an AsyncPaginator immediately;
    # only iteration is asynchronous.  Restrict discovery to markets ending
    # near now so a live collector does not paginate the entire Gamma catalog.
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    paginator = client.list_markets(
        closed=False,
        end_date_min=(reference - timedelta(minutes=5)).isoformat(),
        end_date_max=(reference + timedelta(minutes=10)).isoformat(),
        order="endDate",
        ascending=True,
        page_size=100,
    )
    identities: list[MarketIdentity] = []
    async for page in paginator:
        for market in page.items:
            if hasattr(market, "model_dump"):
                mapping = market.model_dump(mode="json", by_alias=True)
            elif isinstance(market, Mapping):
                mapping = market
            else:
                continue
            identity = market_identity_from_mapping(mapping)
            if identity is not None:
                identities.append(identity)
    return identities


def active_token_ids(
    markets: list[MarketIdentity] | tuple[MarketIdentity, ...],
    *,
    now: datetime,
    grace_seconds: int = 60,
) -> list[str]:
    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    now_us = int(reference.astimezone(timezone.utc).timestamp() * 1_000_000)
    cutoff_us = now_us - max(0, grace_seconds) * 1_000_000
    tokens: list[str] = []
    seen: set[str] = set()
    for market in sorted(markets, key=lambda item: item.window_start_ts):
        if market.window_end_ts < cutoff_us:
            continue
        for token_id in (market.up_token_id, market.down_token_id):
            if token_id and token_id not in seen:
                seen.add(token_id)
                tokens.append(token_id)
    return tokens
