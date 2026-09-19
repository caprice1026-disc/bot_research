import csv
import json
from decimal import Decimal

import pytest

from trading_core.market_data.replay_inputs import load_replay_inputs


def _inputs(tmp_path, *, missing_candle=False, missing_funding=False, duplicate=False):
    candles = tmp_path / "candles.jsonl"
    rows = [{"open_time_ms": t, "close_exclusive_ms": t + 60_000,
             "available_at_ms": t + 60_000, "venue": "binance_usdm_public", "symbol": "BTCUSDT",
             "open": "100", "high": "101", "low": "99", "close": "100"}
            for t in range(0, 86_400_000, 60_000)]
    if missing_candle:
        rows.pop(5)
    candles.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    funding = tmp_path / "funding.csv"
    with funding.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["calc_time", "funding_interval_hours", "last_funding_rate"])
        events = [0, 28_800_001, 57_600_000]
        if missing_funding:
            events.pop()
        if duplicate:
            events.append(events[-1])
        writer.writerows((t, 8, "0.0001") for t in events)
    return candles, funding


def test_complete_inputs_round_funding_forward_without_future_candles(tmp_path):
    candles, funding = _inputs(tmp_path)
    ticks, rates, report = load_replay_inputs(candles, funding, 0, 86_400_000)
    assert len(ticks) == 1440
    assert ticks[0].timestamp_ms == 60_000
    assert rates[28_860_000] == Decimal("0.0001")
    assert report["funding_count"] == 3


@pytest.mark.parametrize("defect", ["missing_candle", "missing_funding", "duplicate"])
def test_incomplete_or_duplicate_inputs_are_rejected(tmp_path, defect):
    candles, funding = _inputs(tmp_path, **{defect: True})
    with pytest.raises(ValueError, match="coverage|duplicate"):
        load_replay_inputs(candles, funding, 0, 86_400_000)
