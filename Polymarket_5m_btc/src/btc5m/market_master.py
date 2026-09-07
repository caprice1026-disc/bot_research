from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl

from btc5m.collectors.polymarket import MarketIdentity


def market_identity_row(identity: MarketIdentity) -> dict[str, object]:
    return {
        "slug": identity.slug,
        "condition_id": identity.condition_id,
        "up_token_id": identity.up_token_id,
        "down_token_id": identity.down_token_id,
        "window_start_ts": identity.window_start_ts,
        "window_end_ts": identity.window_end_ts,
    }


def write_market_master(path: Path, identities: Iterable[MarketIdentity]) -> int:
    rows = [market_identity_row(identity) for identity in identities]
    frame = pl.DataFrame(
        rows,
        schema={
            "slug": pl.String,
            "condition_id": pl.String,
            "up_token_id": pl.String,
            "down_token_id": pl.String,
            "window_start_ts": pl.Int64,
            "window_end_ts": pl.Int64,
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    if temporary.exists():
        temporary.unlink()
    frame.write_parquet(temporary, compression="zstd")
    temporary.replace(path)
    return len(rows)
