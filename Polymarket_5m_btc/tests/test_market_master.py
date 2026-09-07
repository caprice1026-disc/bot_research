from __future__ import annotations

import polars as pl

from btc5m.collectors.polymarket import MarketIdentity
from btc5m.market_master import write_market_master


def _market(condition_id: str, start: int) -> MarketIdentity:
    return MarketIdentity(
        slug=f"btc-updown-5m-{start}",
        condition_id=condition_id,
        up_token_id=f"up-{condition_id}",
        down_token_id=f"down-{condition_id}",
        window_start_ts=start,
        window_end_ts=start + 300_000_000,
    )


def test_market_master_upsert_preserves_previous_discovered_markets(tmp_path) -> None:
    path = tmp_path / "markets.parquet"
    write_market_master(path, [_market("old", 1)])

    count = write_market_master(path, [_market("new", 2)])
    rows = pl.read_parquet(path).to_dicts()

    assert count == 2
    assert {row["condition_id"] for row in rows} == {"old", "new"}
