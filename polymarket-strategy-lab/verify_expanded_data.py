"""Check saved expanded BTC features against manifests and raw Binance bars."""
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data"


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    manifest = json.loads((ROOT / "btc_expanded_replay.manifest.json").read_text())
    output = ROOT / "btc_expanded_replay.csv"
    assert sha256(output) == manifest["output_sha256"]
    assert manifest["extracted_hours"] == manifest["requested_hours"] == len(manifest["hours"]) == 30
    assert not manifest["failures"]
    bars, counts, markets = {}, Counter(), set()
    for hour in manifest["hours"]:
        stem = ROOT / "expanded_books" / hour["hour"]
        for suffix, key in ((".csv", "csv_sha256"), (".parquet", "subset_sha256"),
                            (".source.json", "source_manifest_sha256")):
            assert sha256(stem.with_suffix(suffix)) == hour[key], str(stem) + suffix
            counts["files_hash_verified"] += 1
        counts["subset_bytes"] += hour["subset_bytes"]
        counts["binance_expected_seconds"] += hour["binance"]["expected_seconds"]
        counts["binance_actual_seconds"] += hour["binance"]["actual_seconds"]
        counts["binance_missing_seconds"] += hour["binance"]["missing_seconds"]
        for item in hour["binance"]["files"]:
            path = Path(item["file"])
            assert sha256(path) == item["sha256"], str(path)
            counts["files_hash_verified"] += 1
            values = json.loads(path.read_text())["body"]
            assert len(values) == item["rows"]
            for bar in values:
                assert int(bar[6]) + 1 - int(bar[0]) == 1000
                bars[(int(bar[6]) + 1) / 1000] = float(bar[4])
    previous = 0
    with output.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            counts["feature_rows"] += 1
            ts = float(row["decision_ts"])
            assert ts > previous
            previous = ts
            assert float(row["window_start_ts"]) <= ts < float(row["window_end_ts"])
            markets.add(row["market_id"])
            valid = []
            for side in ("up", "down"):
                if row[f"{side}_quote_recv_ts"]:
                    assert float(row[f"{side}_quote_recv_ts"]) < ts
                flag = row[f"{side}_book_valid"] == "True"
                valid.append(flag)
                if flag:
                    assert 0 < float(row[f"{side}_bid"]) < float(row[f"{side}_ask"]) < 1
                    assert float(row[f"{side}_bid_size"]) > 0 and float(row[f"{side}_ask_size"]) > 0
                    counts["valid_sides"] += 1
            if all(valid):
                counts["valid_paired_rows"] += 1
                if max(float(row[f"{s}_book_snapshot_age_seconds"]) for s in ("up", "down")) <= 1:
                    counts["valid_paired_rows_snapshot_age_le_1s"] += 1
            assert all(not value for name, value in row.items() if name.startswith(("coinbase_", "chainlink")))
            assert not row["binance_bid"] and not row["binance_ask"]
            source, available = float(row["binance_source_ts"]), float(row["binance_recv_ts"])
            assert source + 1 == available < ts
            assert float(row["binance_price"]) == bars[source]
            assert float(row["binance_age_seconds"]) == ts - available
            assert float(row["binance_twap60_source_ts"]) == source
            assert float(row["binance_twap60_available_ts"]) == available
            mean = sum(bars[source - offset] for offset in range(60)) / 60
            assert math.isclose(float(row["binance_twap60_price"]), mean, rel_tol=1e-12)
    assert counts["feature_rows"] == manifest["counts"]["feature_rows"]
    assert counts["valid_paired_rows"] == manifest["counts"]["valid_paired_rows"]
    assert len(markets) == manifest["market_count"] == 360
    assert counts["binance_missing_seconds"] == 0
    result = {"status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
              "output_sha256": manifest["output_sha256"], "counts": dict(counts),
              "market_count": len(markets), "hours": len(manifest["hours"]),
              "checks": ["All saved subset/CSV/source/Binance hashes match",
                         "Decision timestamps strictly increase and remain within their market windows",
                         "Archived quote receipt precedes decision; valid prices and depth are positive",
                         "Binance values equal real cached bars and use close+1s assumed availability",
                         "All 60-second means use contiguous cached bar closes known before decision",
                         "No fabricated Binance bid/ask, Coinbase or Chainlink values"],
              "limitations": manifest["limitations"]}
    (ROOT / "btc_expanded_integrity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"checks", "limitations"}}, indent=2))


if __name__ == "__main__":
    main()
