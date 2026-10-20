"""Paper-only historical executable-quote opportunity scan.

This deliberately does not submit transactions, invent pool quotes from an
aggregate price series, or report the sum of independent quotes as realized PnL.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

D = Decimal


@dataclass(frozen=True)
class Quote:
    timestamp: str
    block_number: int
    pool_id: str
    amount_in_usdc: Decimal
    amount_out_jpyc: Decimal
    fx_jpy_per_usdc: Decimal
    gas_cost_jpy: Decimal
    source_kind: str
    source: str

    @classmethod
    def parse(cls, row: dict) -> "Quote":
        if row["source_kind"] != "historical_pool_quote":
            raise ValueError("Historical pool quotes required; aggregate/synthetic prices rejected")
        q = cls(
            row["timestamp"], int(row["block_number"]), row["pool_id"],
            D(str(row["amount_in_usdc"])), D(str(row["amount_out_jpyc"])),
            D(str(row["fx_jpy_per_usdc"])), D(str(row["gas_cost_jpy"])),
            row["source_kind"], row["source"],
        )
        dt = datetime.fromisoformat(q.timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timezone-aware timestamp required")
        if q.block_number < 1 or not q.pool_id or not q.source:
            raise ValueError("Missing quote provenance")
        if any(not x.is_finite() for x in
               (q.amount_in_usdc, q.amount_out_jpyc, q.fx_jpy_per_usdc, q.gas_cost_jpy)):
            raise ValueError("Non-finite value")
        if min(q.amount_in_usdc, q.amount_out_jpyc, q.fx_jpy_per_usdc) <= 0 or q.gas_cost_jpy < 0:
            raise ValueError("Invalid quantity, FX or gas")
        return q


def economics(q: Quote, execution_haircut_bps: Decimal = D("0"),
              gas_multiplier: Decimal = D("1")) -> dict:
    """LP fees and own-size impact are already in amount_out_jpyc."""
    if not D(0) <= execution_haircut_bps < D(10000) or gas_multiplier < 0:
        raise ValueError("Invalid sensitivity")
    cost = q.amount_in_usdc * q.fx_jpy_per_usdc
    # One JPY per JPYC is an assumed redemption reference, not a booked receipt.
    proceeds = q.amount_out_jpyc * (1 - execution_haircut_bps / D(10000))
    gas = q.gas_cost_jpy * gas_multiplier
    net = proceeds - cost - gas
    return {"input_reference_jpy": cost, "redemption_reference_jpy": proceeds,
            "swap_gas_jpy": gas, "net_reference_profit_jpy": net,
            "return_on_input_pct": net / cost * 100, "profitable": net > 0}


def scan(rows: list[dict], *, gas_multiplier: Decimal = D(1),
         execution_haircut_bps: Decimal = D(0), capital_usdc: Decimal) -> tuple[list[dict], dict]:
    """Highest net profit on the preset size grid, first snapshot per episode.

    An episode is adjacent positive snapshots in this supplied sample sequence.
    Gaps > 5 minutes split episodes. This suppresses immediate duplicates but
    DOES NOT model how hypothetical execution changes future historical states.
    Inventory is locked, never automatically redeemed/recycled in this scan.
    """
    if not capital_usdc.is_finite() or capital_usdc <= 0:
        raise ValueError("Positive explicit capital limit required")
    groups: dict[tuple, list[Quote]] = defaultdict(list)
    for row in rows:
        q = Quote.parse(row)
        groups[(q.timestamp, q.block_number, q.pool_id)].append(q)
    if len({key[2] for key in groups}) > 1:
        raise ValueError("Only one pool per scan is supported")
    output = []
    locked = D(0)
    previous_time = None
    previous_positive = False
    episode = 0
    selected_count = 0
    eligible_count = 0
    for key, quotes in sorted(groups.items(), key=lambda item: datetime.fromisoformat(item[0][0].replace("Z", "+00:00"))):
        now = datetime.fromisoformat(key[0].replace("Z", "+00:00"))
        if len({q.amount_in_usdc for q in quotes}) != len(quotes):
            raise ValueError("Duplicate size within a snapshot")
        costs = [(q, economics(q, execution_haircut_bps, gas_multiplier))
                 for q in sorted(quotes, key=lambda q: q.amount_in_usdc)]
        positives = [(q, e) for q, e in costs if e["profitable"]]
        positive = bool(positives)
        if positive:
            eligible_count += 1
        new_episode = positive and (
            not previous_positive or previous_time is None
            or (now - previous_time).total_seconds() > 300
        )
        if new_episode:
            episode += 1
        affordable = [(q, e) for q, e in positives
                      if q.amount_in_usdc + locked <= capital_usdc]
        chosen = (max(affordable,
                      key=lambda pair: (pair[1]["net_reference_profit_jpy"], -pair[0].amount_in_usdc))[0]
                  if affordable and new_episode else None)
        if chosen is not None:
            locked += chosen.amount_in_usdc
            selected_count += 1
        for q, e in costs:
            record = {
                "timestamp": q.timestamp, "block_number": q.block_number,
                "pool_id": q.pool_id, "amount_in_usdc": str(q.amount_in_usdc),
                "amount_out_jpyc": str(q.amount_out_jpyc),
                "fx_jpy_per_usdc": str(q.fx_jpy_per_usdc),
                **{k: str(v) if isinstance(v, Decimal) else v for k, v in e.items()},
                "candidate_episode": episode if positive else "",
                "conditional_selected_candidate": q is chosen,
                "capital_locked_usdc_after_snapshot": str(locked),
                "source": q.source,
            }
            output.append(record)
        previous_positive, previous_time = positive, now
    summary = {
        "status": "quote_scan_complete" if rows else "insufficient_historical_quotes",
        "classification": "independent_historical_quote_opportunity_scan_not_realized_backtest",
        "snapshots": len(groups), "quote_rows": len(rows),
        "positive_snapshots": eligible_count if rows else None, "candidate_episodes": episode if rows else None,
        "conditional_selected_candidates": selected_count if rows else None,
        "initial_capital_usdc": str(capital_usdc), "locked_input_usdc": str(locked),
        "gas_multiplier": str(gas_multiplier),
        "execution_haircut_bps": str(execution_haircut_bps),
        "realized_profit_jpy": None,
        "cumulative_strategy_profit_jpy": None,
        "limitations": [
            "Historical snapshots do not include the hypothetical trader's prior self-impact.",
            "Preset size grid chooses highest positive net profit at the current snapshot only, not a future-best entry.",
            "All quotes include LP fee and price impact; neither is deducted twice.",
            "No acquisition/remittance fees; swap gas is retained unless sensitivity sets zero.",
            "1 JPY redemption is a reference assumption, not an executed bank redemption.",
            "Inventory stays locked; no automatic instant recycling or unlimited capital.",
            "MEV, reverted gas and actual future slippage are not measured.",
            "A Quoter gas estimate alone is not a full transaction gas estimate.",
        ],
    }
    return output, summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_json")
    p.add_argument("--capital-usdc", required=True)
    p.add_argument("--gas-multiplier", default="1")
    p.add_argument("--execution-haircut-bps", default="0")
    p.add_argument("--output-prefix", required=True)
    a = p.parse_args()
    rows = json.loads(Path(a.input_json).read_text())
    result, summary = scan(rows, capital_usdc=D(a.capital_usdc),
                           gas_multiplier=D(a.gas_multiplier),
                           execution_haircut_bps=D(a.execution_haircut_bps))
    prefix = Path(a.output_prefix)
    if result:
        with prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(result[0]))
            w.writeheader()
            w.writerows(result)
    prefix.with_suffix(".json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
