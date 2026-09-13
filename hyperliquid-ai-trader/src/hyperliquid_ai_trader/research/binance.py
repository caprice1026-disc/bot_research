"""Validated, local-only readers for Binance USD-M research artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .data import CANDLE_INTERVAL_MS, NormalizedCandle, ResearchDataError, validate_contiguous_candles


BINANCE_USDM_VENUE = "binance_usdm_public"
BINANCE_USDM_SYMBOL = "BTCUSDT"
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1_000


class FundingDataError(ValueError):
    """Raised when official funding data cannot support a simulated episode."""


@dataclass(frozen=True)
class FundingEvent:
    timestamp_ms: int
    rate: Decimal

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise FundingDataError("funding timestamp must be non-negative")
        if not self.rate.is_finite():
            raise FundingDataError("funding rate must be finite")


@dataclass(frozen=True)
class FundingSeries:
    """Official Binance funding rates, keyed by their nominal eight-hour slot."""

    events: tuple[FundingEvent, ...]

    def __post_init__(self) -> None:
        slots = [event.timestamp_ms // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS for event in self.events]
        if len(set(slots)) != len(slots):
            raise FundingDataError("duplicate funding event slot")

    def payment(
        self,
        *,
        entry_time_ms: int,
        exit_time_ms: int,
        notional: Decimal,
        side: str,
    ) -> Decimal:
        if exit_time_ms < entry_time_ms:
            raise FundingDataError("funding exit precedes entry")
        if notional < 0 or notional.is_finite() is False:
            raise FundingDataError("funding notional must be finite and non-negative")
        if side not in {"long", "short"}:
            raise FundingDataError("funding side must be long or short")
        first_slot = (entry_time_ms // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
        expected_slots = range(first_slot, exit_time_ms + 1, FUNDING_INTERVAL_MS)
        rates = {
            event.timestamp_ms // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS: event.rate
            for event in self.events
        }
        payment = Decimal("0")
        for slot in expected_slots:
            if slot not in rates:
                raise FundingDataError(f"missing funding event for scheduled slot {slot}")
            # Positive Binance funding is paid by longs and received by shorts.
            payment += (-notional if side == "long" else notional) * rates[slot]
        return payment


def _required_columns(reader: csv.DictReader, columns: set[str], *, kind: str) -> None:
    if reader.fieldnames is None or not columns.issubset(set(reader.fieldnames)):
        raise ResearchDataError(f"{kind} CSV has missing required columns")


def read_binance_usdm_1m_csv(
    path: Path, *, delivery_delay_ms: int = 0
) -> list[NormalizedCandle]:
    """Convert the sibling downloader's verified BTCUSDT 1m CSV to JSONL rows."""

    if delivery_delay_ms < 0:
        raise ResearchDataError("delivery_delay_ms must be non-negative")
    required = {"open_time_ms", "open", "high", "low", "close", "volume", "close_time_ms"}
    candles: list[NormalizedCandle] = []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            _required_columns(reader, required, kind="Binance kline")
            for line_number, row in enumerate(reader, start=2):
                try:
                    open_time_ms = int(row["open_time_ms"])
                    close_time_ms = int(row["close_time_ms"])
                    if close_time_ms != open_time_ms + CANDLE_INTERVAL_MS - 1:
                        raise ValueError("close_time_ms is not one minute inclusive")
                    close_exclusive_ms = close_time_ms + 1
                    candles.append(
                        NormalizedCandle(
                            venue=BINANCE_USDM_VENUE,
                            symbol=BINANCE_USDM_SYMBOL,
                            open_time_ms=open_time_ms,
                            close_exclusive_ms=close_exclusive_ms,
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            received_at_ms=close_exclusive_ms + delivery_delay_ms,
                            available_at_ms=close_exclusive_ms + delivery_delay_ms,
                            availability_kind="binance_historical_close_plus_delay",
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ResearchDataError(f"invalid Binance kline at line {line_number}") from error
    except OSError as error:
        raise ResearchDataError(f"cannot read Binance kline CSV: {path}") from error
    if not candles:
        raise ResearchDataError("Binance kline CSV is empty")
    validate_contiguous_candles(candles)
    return candles


def read_binance_usdm_funding_csv(path: Path) -> FundingSeries:
    """Read the official funding CSV produced by the sibling research collector."""

    required = {"calc_time", "funding_interval_hours", "last_funding_rate"}
    events: list[FundingEvent] = []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            _required_columns(reader, required, kind="Binance funding")
            for line_number, row in enumerate(reader, start=2):
                try:
                    if Decimal(str(row["funding_interval_hours"])) != Decimal("8"):
                        raise ValueError("funding interval must be eight hours")
                    events.append(
                        FundingEvent(
                            timestamp_ms=int(row["calc_time"]),
                            rate=Decimal(str(row["last_funding_rate"])),
                        )
                    )
                except (InvalidOperation, KeyError, TypeError, ValueError) as error:
                    raise FundingDataError(f"invalid Binance funding row at line {line_number}") from error
    except OSError as error:
        raise FundingDataError(f"cannot read Binance funding CSV: {path}") from error
    if not events:
        raise FundingDataError("Binance funding CSV is empty")
    return FundingSeries(tuple(events))
