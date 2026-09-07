from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ALL_SOURCES = ("polymarket", "chainlink", "binance", "coinbase", "hyperliquid")


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    sources: tuple[str, ...] = ALL_SOURCES
    duration_seconds: int = 60
    output_root: Path = Path("data/raw_staging")

    def __post_init__(self) -> None:
        if self.duration_seconds < 1:
            raise ValueError("duration_seconds must be at least 1")
        unknown = set(self.sources) - set(ALL_SOURCES)
        if unknown:
            raise ValueError(f"unknown sources: {sorted(unknown)}")


def parse_sources(value: str) -> tuple[str, ...]:
    sources = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not sources or sources == ("all",):
        return ALL_SOURCES
    return sources
