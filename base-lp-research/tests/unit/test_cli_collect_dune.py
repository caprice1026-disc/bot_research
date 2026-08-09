import json
import shutil
import unittest
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from base_lp.cli import command_collect_dune
from base_lp.data.dune import DuneError
from base_lp.data.events import SWAP_TOPIC
from base_lp.reporting import write_json
from base_lp.schemas import PoolMetadata


def word(value: int) -> str:
    if value >= 0:
        return value.to_bytes(32, "big").hex()
    return ((1 << 256) + value).to_bytes(32, "big").hex()


class FakeDune:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = []
        self.waited_for = []
        self.paged_for = []

    def execute_sql(self, sql):
        self.executed_sql.append(sql)
        return "dune-execution-1"

    def wait_for_completion(self, execution_id, *, poll_interval_seconds, timeout_seconds):
        self.waited_for.append((execution_id, poll_interval_seconds, timeout_seconds))
        return {"state": "QUERY_STATE_COMPLETED"}

    def iter_result_rows(self, execution_id, *, page_size, columns=None):
        self.paged_for.append((execution_id, page_size, columns))
        yield from self.rows


class CollectDuneTests(unittest.TestCase):
    def test_collect_dune_writes_decoder_compatible_swap_dataset_and_provenance(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp" / "cli-collect-dune"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            pool = PoolMetadata(
                chain_id=8453,
                pool_address="0xd0b53d9277642d899df5c87a3966a349a798f224",
                token0="0x4200000000000000000000000000000000000006",
                token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                token0_decimals=18,
                token1_decimals=6,
                fee_tier=500,
                tick_spacing=10,
                creation_block=0,
            )
            write_json(root / "results" / "pool_metadata.json", asdict(pool))
            write_json(
                root / "results" / "dune_collection_checkpoint.json",
                {
                    "status": "submitted",
                    "execution_id": "older-execution",
                    "pool_address": pool.pool_address,
                    "start_utc": "2026-05-01T00:00:00Z",
                    "end_utc": "2026-06-01T00:00:00Z",
                    "sql_sha256": "old-sql",
                },
            )
            rows = [
                {
                    "block_time": "2026-06-01 08:11:29.000 UTC",
                    "block_number": 46756071,
                    "block_hash": "0x" + "aa" * 32,
                    "contract_address": pool.pool_address,
                    "topic0": SWAP_TOPIC,
                    "topic1": "0x" + "00" * 32,
                    "topic2": "0x" + "11" * 32,
                    "topic3": None,
                    "data": "0x" + "".join([word(-10), word(20), word(2**96), word(1000), word(0)]),
                    "tx_hash": "0x" + "bb" * 32,
                    "log_index": 7,
                    "tx_index": 3,
                }
            ]
            dune = FakeDune(rows)
            args = Namespace(
                root=str(root),
                config=str(Path(__file__).resolve().parents[2] / "configs" / "base_weth_usdc_005.yaml"),
                start_utc="2026-06-01T00:00:00Z",
                end_utc="2026-07-01T00:00:00Z",
                page_size=10_000,
                poll_interval_seconds=5.0,
                max_wait_seconds=900.0,
            )

            with patch("base_lp.cli._dune_client", return_value=dune):
                self.assertEqual(command_collect_dune(args), 0)

            manifest = json.loads((root / "results" / "dataset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["source"], "dune-sql")
            self.assertEqual(manifest["row_counts"], {"logs": 1, "swaps": 1})
            self.assertEqual(manifest["metadata"]["dune_execution_id"], "dune-execution-1")
            self.assertEqual(len(manifest["metadata"]["sql_sha256"]), 64)
            self.assertEqual(len(dune.executed_sql), 1)
            self.assertIn(SWAP_TOPIC, dune.executed_sql[0])
            self.assertTrue((root / "data" / "normalized" / "events.parquet").exists())
            archived = (root / "results" / "dune_collection_history.jsonl").read_text(encoding="utf-8")
            self.assertIn("older-execution", archived)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_collect_dune_records_a_collection_error_without_reexecuting_sql(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp" / "cli-collect-dune-error"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            pool = PoolMetadata(
                chain_id=8453,
                pool_address="0xd0b53d9277642d899df5c87a3966a349a798f224",
                token0="0x4200000000000000000000000000000000000006",
                token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                token0_decimals=18,
                token1_decimals=6,
                fee_tier=500,
                tick_spacing=10,
                creation_block=0,
            )
            write_json(root / "results" / "pool_metadata.json", asdict(pool))

            class LimitExceededDune(FakeDune):
                def iter_result_rows(self, execution_id, *, page_size, columns=None):
                    raise DuneError("This api request would exceed your configured datapoint limit per billing cycle")

            dune = LimitExceededDune([])
            args = Namespace(
                root=str(root),
                config=str(Path(__file__).resolve().parents[2] / "configs" / "base_weth_usdc_005.yaml"),
                start_utc="2026-06-01T00:00:00Z",
                end_utc="2026-06-08T00:00:00Z",
                page_size=10_000,
                poll_interval_seconds=5.0,
                max_wait_seconds=900.0,
            )

            with patch("base_lp.cli._dune_client", return_value=dune):
                self.assertEqual(command_collect_dune(args), 2)

            manifest = json.loads((root / "results" / "dataset_manifest.json").read_text(encoding="utf-8"))
            checkpoint = json.loads((root / "results" / "dune_collection_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "collection_error")
            self.assertEqual(manifest["metadata"]["dune_execution_id"], "dune-execution-1")
            self.assertIn("datapoint limit", manifest["errors"][0])
            self.assertEqual(checkpoint["status"], "result_error")
            self.assertEqual(len(dune.executed_sql), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
