from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trading_core.market_data.catalog import CatalogError, resolve_dataset, verify_dataset


def _row(index: int) -> dict[str, object]:
    return {
        "open_time_ms": index * 60_000,
        "close_exclusive_ms": (index + 1) * 60_000,
    }


def _catalog(tmp_path: Path) -> tuple[Path, str]:
    data = tmp_path / "normalized" / "btc.jsonl"
    data.parent.mkdir()
    data.write_text("\n".join(json.dumps(_row(index)) for index in range(3)) + "\n", encoding="utf-8")
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "datasets": [
                    {
                        "dataset_id": "binance-btc-1m-fixture",
                        "path": "normalized/btc.jsonl",
                        "venue": "binance_usdm_public",
                        "symbol": "BTCUSDT",
                        "interval_ms": 60_000,
                        "start_ms": 0,
                        "end_ms": 180_000,
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return catalog, digest


def test_catalog_resolves_only_the_expected_content_and_verifies_continuity(tmp_path: Path) -> None:
    catalog, digest = _catalog(tmp_path)

    reference = resolve_dataset(catalog, "binance-btc-1m-fixture", digest)
    coverage = verify_dataset(reference)

    assert reference.path.name == "btc.jsonl"
    assert coverage.status == "complete"
    assert coverage.observed_start_ms == 0
    assert coverage.observed_end_ms == 180_000
    assert coverage.gaps == ()


def test_catalog_rejects_a_wrong_hash_before_an_experiment_can_read_the_file(tmp_path: Path) -> None:
    catalog, _ = _catalog(tmp_path)

    with pytest.raises(CatalogError, match="expected SHA-256"):
        resolve_dataset(catalog, "binance-btc-1m-fixture", "0" * 64)


def test_catalog_marks_a_gap_in_normalized_one_minute_data_incomplete(tmp_path: Path) -> None:
    catalog, digest = _catalog(tmp_path)
    data = tmp_path / "normalized" / "btc.jsonl"
    data.write_text(json.dumps(_row(0)) + "\n" + json.dumps(_row(2)) + "\n", encoding="utf-8")
    changed = hashlib.sha256(data.read_bytes()).hexdigest()
    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    catalog_payload["datasets"][0]["sha256"] = changed
    catalog.write_text(json.dumps(catalog_payload), encoding="utf-8")

    coverage = verify_dataset(resolve_dataset(catalog, "binance-btc-1m-fixture", changed))

    assert digest != changed
    assert coverage.status == "incomplete"
    assert coverage.gaps == ((60_000, 120_000),)
