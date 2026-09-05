"""Secret-safe performance report derived from confirmed exchange evidence."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from .storage import SQLiteStore


def _max_drawdown(equities: list[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak * 100.0)
    return maximum


def _bucket(confidence: float) -> str:
    low = min(int(confidence * 10) / 10.0, 0.9)
    return f"{low:.1f}-{low + 0.1:.1f}"


def generate_report(store: SQLiteStore, run_id: str) -> dict[str, Any]:
    run = store.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("run does not exist")
    fills = store.connection.execute(
        "SELECT * FROM fills WHERE run_id=? ORDER BY timestamp_ms, id",
        (run_id,),
    ).fetchall()
    funding_rows = store.connection.execute(
        "SELECT amount FROM funding_payments WHERE run_id=?",
        (run_id,),
    ).fetchall()
    equity_rows = store.connection.execute(
        "SELECT equity FROM equity_snapshots WHERE run_id=? ORDER BY timestamp_ms, id",
        (run_id,),
    ).fetchall()
    excursion_rows = store.connection.execute(
        "SELECT mfe_pct, mae_pct FROM episode_metrics WHERE run_id=? ORDER BY slot",
        (run_id,),
    ).fetchall()
    cycle_rows = store.connection.execute(
        "SELECT slot, scheduled_at_ms, strategy_version, decision_json, status FROM cycles WHERE run_id=? ORDER BY slot",
        (run_id,),
    ).fetchall()

    gross_pnl = sum(float(row["closed_pnl"]) for row in fills)
    fees = sum(float(row["fee"]) for row in fills)
    funding = sum(float(row["amount"]) for row in funding_rows)
    net_pnl = gross_pnl - fees + funding
    slot_metrics: dict[int, dict[str, float]] = defaultdict(lambda: {"gross": 0.0, "fees": 0.0})
    for row in fills:
        slot = int(row["slot"])
        slot_metrics[slot]["gross"] += float(row["closed_pnl"])
        slot_metrics[slot]["fees"] += float(row["fee"])
    slot_net = {
        slot: values["gross"] - values["fees"]
        for slot, values in slot_metrics.items()
    }
    closed_slots = [slot for slot, values in slot_metrics.items() if values["gross"] != 0]
    closed = [slot_net[slot] for slot in closed_slots]
    wins = [value for value in closed if value > 0]
    losses = [value for value in closed if value < 0]
    win_rate = len(wins) / len(closed) if closed else None
    profit_factor = sum(wins) / abs(sum(losses)) if losses else None

    cycle_by_slot = {int(row["slot"]): row for row in cycle_rows}
    pnl_by_side: dict[str, float] = defaultdict(float)
    for slot in closed_slots:
        cycle = cycle_by_slot.get(slot)
        decision = json.loads(cycle["decision_json"]) if cycle and cycle["decision_json"] else {}
        side = str(decision.get("side") or "unknown")
        pnl_by_side[side] += slot_net[slot]

    def _empty_group() -> dict[str, float | int]:
        return {"cycles": 0, "gross_closed_pnl": 0.0, "fees": 0.0, "net_closed_pnl": 0.0}

    def _add_group(groups: dict[str, dict[str, float | int]], key: str, slot: int) -> None:
        values = slot_metrics.get(slot, {"gross": 0.0, "fees": 0.0})
        group = groups.setdefault(key, _empty_group())
        group["cycles"] += 1
        group["gross_closed_pnl"] += values["gross"]
        group["fees"] += values["fees"]
        group["net_closed_pnl"] += values["gross"] - values["fees"]

    abstain_groups: dict[str, dict[str, float | int]] = {"true": _empty_group(), "false": _empty_group()}
    strategy_groups: dict[str, dict[str, float | int]] = {}
    confidence_groups: dict[str, dict[str, float | int]] = {}
    statuses: dict[str, int] = defaultdict(int)
    for row in cycle_rows:
        statuses[str(row["status"])] += 1
        if not row["decision_json"]:
            continue
        decision = json.loads(row["decision_json"])
        slot = int(row["slot"])
        abstain_key = "true" if decision.get("would_abstain") else "false"
        _add_group(abstain_groups, abstain_key, slot)
        version = str(row["strategy_version"])
        _add_group(strategy_groups, version, slot)
        confidence = float(decision.get("confidence", 0))
        confidence_key = _bucket(confidence)
        _add_group(confidence_groups, confidence_key, slot)

    initial_equity = float(run["initial_equity"])
    final_equity = float(run["final_equity"] or run["initial_equity"])
    initial_mark = float(run["initial_mark"])
    final_mark = float(run["final_mark"] or run["initial_mark"])
    equities = [float(row["equity"]) for row in equity_rows]
    if not equities:
        equities = [initial_equity, final_equity]
    mfe_values = [float(row["mfe_pct"]) for row in excursion_rows]
    mae_values = [float(row["mae_pct"]) for row in excursion_rows]

    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": run["status"],
        "mode": run["mode"],
        "git_sha": run["git_sha"],
        "started_at_ms": run["started_at_ms"],
        "completed_at_ms": run["completed_at_ms"],
        "equity": {"initial": initial_equity, "final": final_equity, "change": final_equity - initial_equity},
        "performance": {
            "gross_pnl": gross_pnl,
            "fees": fees,
            "funding": funding,
            "net_pnl": net_pnl,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": _max_drawdown(equities),
            "closed_episode_count": len(closed_slots),
        },
        "direction": dict(sorted(pnl_by_side.items())),
        "confidence_buckets": dict(sorted(confidence_groups.items())),
        "would_abstain": abstain_groups,
        "strategy_versions": dict(sorted(strategy_groups.items(), key=lambda item: int(item[0]))),
        "excursion": {
            "episode_count": len(excursion_rows),
            "average_mfe_pct": sum(mfe_values) / len(mfe_values) if mfe_values else None,
            "average_mae_pct": sum(mae_values) / len(mae_values) if mae_values else None,
            "method": "1m_candle_estimate",
        },
        "cycle_statuses": dict(sorted(statuses.items())),
        "benchmarks": {
            "btc_buy_and_hold_pnl": initial_equity * (final_mark / initial_mark - 1.0),
            "usdc_flat_pnl": 0.0,
        },
        "measurement_notes": {
            "pnl": "confirmed fills, fees, and user funding only; grouped net_closed_pnl includes fill fees and excludes run-level funding",
            "mfe_mae": "MFE/MAE is estimated from 1m candles when available; it is not tick-exact",
        },
    }


def write_report(report: dict[str, Any], prefix: str | Path) -> tuple[Path, Path]:
    prefix_path = Path(prefix)
    prefix_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix_path.with_suffix(".json")
    markdown_path = prefix_path.with_suffix(".md")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    performance = report["performance"]
    equity = report["equity"]
    benchmark = report["benchmarks"]
    excursion = report["excursion"]
    average_mfe = excursion["average_mfe_pct"]
    average_mae = excursion["average_mae_pct"]
    average_mfe_text = "n/a" if average_mfe is None else f"{average_mfe:.6f}%"
    average_mae_text = "n/a" if average_mae is None else f"{average_mae:.6f}%"
    markdown = f"""# Hyperliquid AI Trader 実験レポート

- Run ID: `{report['run_id']}`
- Status: `{report['status']}`
- Mode: `{report['mode']}`
- Git SHA: `{report['git_sha']}`

## Performance

- Initial equity: {equity['initial']:.6f} USDC
- Final equity: {equity['final']:.6f} USDC
- Gross PnL: {performance['gross_pnl']:.6f} USDC
- Fees: {performance['fees']:.6f} USDC
- Funding: {performance['funding']:.6f} USDC
- Net PnL: {performance['net_pnl']:.6f} USDC
- Win rate: {performance['win_rate']}
- Profit factor: {performance['profit_factor']}
- Max drawdown: {performance['max_drawdown_pct']:.6f}%
- Estimated MFE (average): {average_mfe_text}
- Estimated MAE (average): {average_mae_text}
- Excursion episodes: {excursion['episode_count']}

## Benchmarks

- BTC Buy & Hold PnL: {benchmark['btc_buy_and_hold_pnl']:.6f} USDC
- USDC Flat PnL: {benchmark['usdc_flat_pnl']:.6f} USDC

## Measurement notes

- PnLは確定fills、fees、user fundingだけを集計する。
- MFE/MAEは利用可能な1分足からの推定値であり、tick単位の実測ではない。
"""
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path
