from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import uuid

import pytest

from hyperliquid_ai_trader.report import generate_report, write_report
from hyperliquid_ai_trader.storage import SQLiteStore


def _directory() -> Path:
    path = Path.cwd() / f"pytest-cache-files-report-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_report_uses_fills_fees_funding_and_equity_drawdown() -> None:
    directory = _directory()
    store = SQLiteStore(directory / "trader.db")
    store.create_run(
        run_id="report-run",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    for slot, decision, strategy_version in [
        (0, {"side": "long", "confidence": "0.7", "would_abstain": False}, 1),
        (1, {"side": "short", "confidence": "0.6", "would_abstain": True}, 2),
    ]:
        assert store.reserve_cycle(
            "report-run",
            slot,
            scheduled_at_ms=1_000 + slot,
            strategy_version=strategy_version,
        )
        store.complete_cycle(
            run_id="report-run",
            slot=slot,
            status="ordered",
            features={"mid": 50000 + slot},
            decision=decision,
            prompt_hash="a" * 64,
            model="gemini-test",
            error_type=None,
        )
    store.record_fill(
        run_id="report-run",
        slot=0,
        fill_id="entry-0",
        side="long",
        size=Decimal("0.005"),
        price=Decimal("50000"),
        fee=Decimal("0.2"),
        closed_pnl=Decimal("0"),
        timestamp_ms=1_100,
    )
    store.record_fill(
        run_id="report-run",
        slot=0,
        fill_id="close-0",
        side="long",
        size=Decimal("0.005"),
        price=Decimal("51000"),
        fee=Decimal("0.2"),
        closed_pnl=Decimal("5"),
        timestamp_ms=1_200,
    )
    store.record_fill(
        run_id="report-run",
        slot=1,
        fill_id="close-1",
        side="short",
        size=Decimal("0.005"),
        price=Decimal("50500"),
        fee=Decimal("0.2"),
        closed_pnl=Decimal("-2"),
        timestamp_ms=1_300,
    )
    store.record_funding(
        run_id="report-run",
        funding_id="funding-1",
        amount=Decimal("0.1"),
        timestamp_ms=1_250,
    )
    store.record_episode_metric(
        run_id="report-run",
        slot=0,
        mfe_pct=Decimal("1.2"),
        mae_pct=Decimal("0.4"),
        method="1m_candle_estimate",
    )
    store.record_episode_metric(
        run_id="report-run",
        slot=1,
        mfe_pct=Decimal("0.8"),
        mae_pct=Decimal("0.6"),
        method="1m_candle_estimate",
    )
    for index, equity in enumerate(("1000", "1010", "1005")):
        store.record_equity(
            run_id="report-run",
            timestamp_ms=1_000 + index,
            equity=Decimal(equity),
            withdrawable=Decimal(equity),
            mark=Decimal("50000") + index,
        )
    store.finish_run(
        run_id="report-run",
        completed_at_ms=2_000,
        final_equity=Decimal("1002.5"),
        final_mark=Decimal("51000"),
        status="completed",
    )

    report = generate_report(store, "report-run")

    assert report["performance"]["gross_pnl"] == pytest.approx(3.0)
    assert report["performance"]["fees"] == pytest.approx(0.6)
    assert report["performance"]["funding"] == pytest.approx(0.1)
    assert report["performance"]["net_pnl"] == pytest.approx(2.5)
    assert report["performance"]["win_rate"] == pytest.approx(0.5)
    assert report["performance"]["profit_factor"] == pytest.approx(2.5)
    assert report["performance"]["max_drawdown_pct"] == pytest.approx(100 * 5 / 1010)
    assert report["benchmarks"]["btc_buy_and_hold_pnl"] == pytest.approx(20.0)
    assert report["benchmarks"]["usdc_flat_pnl"] == 0.0
    assert report["would_abstain"]["true"]["net_closed_pnl"] == pytest.approx(-2.0)
    assert report["strategy_versions"]["1"]["net_closed_pnl"] == pytest.approx(5.0)
    assert report["excursion"]["episode_count"] == 2
    assert report["excursion"]["average_mfe_pct"] == pytest.approx(1.0)
    assert report["excursion"]["average_mae_pct"] == pytest.approx(0.5)

    json_path, markdown_path = write_report(report, directory / "summary")
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "report-run"
    assert "MFE/MAE" in markdown_path.read_text(encoding="utf-8")
    store.close()
