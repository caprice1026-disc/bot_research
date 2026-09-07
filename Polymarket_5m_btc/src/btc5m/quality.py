from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from btc5m.events import parse_source_timestamp


@dataclass(frozen=True, slots=True)
class QualityResult:
    total_rows: int
    missing_local_receive_count: int = 0
    duplicate_sequence_count: int = 0
    timestamp_inversion_count: int = 0
    negative_spread_count: int = 0
    crossed_book_count: int = 0
    parse_error_count: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return self.total_rows > 0 and not any(
            (
                self.missing_local_receive_count,
                self.duplicate_sequence_count,
                self.timestamp_inversion_count,
                self.negative_spread_count,
                self.crossed_book_count,
                self.parse_error_count,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "total_rows": self.total_rows,
            "missing_local_receive_count": self.missing_local_receive_count,
            "duplicate_sequence_count": self.duplicate_sequence_count,
            "timestamp_inversion_count": self.timestamp_inversion_count,
            "negative_spread_count": self.negative_spread_count,
            "crossed_book_count": self.crossed_book_count,
            "parse_error_count": self.parse_error_count,
            "errors": list(self.errors),
        }


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def validate_events(rows: Iterable[Mapping[str, object]]) -> QualityResult:
    total = 0
    missing_receive = 0
    duplicate_sequence = 0
    timestamp_inversion = 0
    negative_spread = 0
    crossed_book = 0
    parse_errors = 0
    errors: list[str] = []
    seen_sequences: set[tuple[str, str, str, str, str]] = set()
    previous_timestamps: dict[str, int] = {}

    for index, row in enumerate(rows):
        total += 1
        source = str(row.get("source") or "")
        receive = row.get("local_receive_ts")
        if receive in (None, ""):
            missing_receive += 1
            errors.append(f"row {index}: missing local_receive_ts")
        else:
            try:
                current = parse_source_timestamp(receive)
                if current is None:
                    raise ValueError("empty timestamp")
                previous = previous_timestamps.get(source)
                if previous is not None and current < previous:
                    timestamp_inversion += 1
                    errors.append(f"row {index}: timestamp inversion for {source}")
                previous_timestamps[source] = current
            except (TypeError, ValueError):
                parse_errors += 1
                errors.append(f"row {index}: invalid local_receive_ts")

        sequence_id = row.get("sequence_id")
        if sequence_id not in (None, ""):
            key = (
                source,
                str(row.get("symbol") or ""),
                str(row.get("market_id") or ""),
                str(row.get("event_type") or ""),
                str(sequence_id),
            )
            if key in seen_sequences:
                duplicate_sequence += 1
                errors.append(
                    f"row {index}: duplicate sequence "
                    f"{source}/{row.get('symbol') or ''}/{row.get('event_type') or ''}/{sequence_id}"
                )
            seen_sequences.add(key)

        bid = _decimal(row.get("bid"))
        ask = _decimal(row.get("ask"))
        if row.get("bid") not in (None, "") and bid is None:
            parse_errors += 1
            errors.append(f"row {index}: invalid bid")
        if row.get("ask") not in (None, "") and ask is None:
            parse_errors += 1
            errors.append(f"row {index}: invalid ask")
        if bid is not None and ask is not None:
            if bid < 0 or ask < 0:
                negative_spread += 1
                errors.append(f"row {index}: negative quote")
            if bid > ask:
                crossed_book += 1
                errors.append(f"row {index}: crossed book")

    return QualityResult(
        total_rows=total,
        missing_local_receive_count=missing_receive,
        duplicate_sequence_count=duplicate_sequence,
        timestamp_inversion_count=timestamp_inversion,
        negative_spread_count=negative_spread,
        crossed_book_count=crossed_book,
        parse_error_count=parse_errors,
        errors=tuple(errors[:20]),
    )
