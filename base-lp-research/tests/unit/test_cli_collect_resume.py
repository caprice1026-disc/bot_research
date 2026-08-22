import json
import shutil
import unittest
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from base_lp.cli import command_collect
from base_lp.data.events import SWAP_TOPIC
from base_lp.reporting import write_json
from base_lp.schemas import PoolMetadata


def word(value: int) -> str:
    if value >= 0:
        return value.to_bytes(32, "big").hex()
    return ((1 << 256) + value).to_bytes(32, "big").hex()


class FakeRpc:
    def __init__(self):
        self.log_ranges = []
        self.log_topics = []

    def latest_block(self):
        return 10

    def request(self, method, params):
        if method == "eth_getLogs":
            query = params[0]
            start = int(query["fromBlock"], 16)
            end = int(query["toBlock"], 16)
            self.log_ranges.append((start, end))
            self.log_topics.append(query.get("topics"))
            return [
                {
                    "blockNumber": hex(start),
                    "transactionIndex": "0x0",
                    "logIndex": "0x0",
                    "blockHash": f"0xblock{start}",
                    "transactionHash": f"0xtx{start}",
                    "address": "0x" + "ab" * 20,
                    "topics": [SWAP_TOPIC, "0x" + "00" * 32, "0x" + "00" * 32],
                    "data": "0x" + "".join([word(-10), word(20), word(2**96), word(1000), word(0)]),
                }
            ]
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(int(params[0], 16) + 100)}
        raise AssertionError(method)


class CollectResumeTests(unittest.TestCase):
    def test_collect_resumes_from_checkpoint_without_refetching_completed_chunks(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp" / "cli-collect-resume"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            pool = PoolMetadata(
                chain_id=8453,
                pool_address="0x" + "ab" * 20,
                token0="0x" + "01" * 20,
                token1="0x" + "02" * 20,
                token0_decimals=18,
                token1_decimals=6,
                fee_tier=500,
                tick_spacing=10,
                creation_block=0,
            )
            write_json(root / "results" / "pool_metadata.json", asdict(pool))
            fake_rpc = FakeRpc()
            args = Namespace(
                root=str(root),
                config=str(Path(__file__).resolve().parents[2] / "configs" / "base_weth_usdc_005.yaml"),
                chunk_size=1,
                start_utc="2026-07-31T00:00:00Z",
                end_utc="2026-07-31T00:01:00Z",
                start_block=1,
                end_block=3,
                request_interval_seconds=0.0,
                rpc_timeout_seconds=None,
                rpc_max_retries=None,
                max_seconds=1.0,
                fresh=False,
            )
            with patch("base_lp.cli._client", return_value=fake_rpc), patch(
                "base_lp.cli.timestamp_to_block", side_effect=[1, 3]
            ), patch("base_lp.cli.time.monotonic", side_effect=[0.0, 0.0, 2.0]), patch(
                "base_lp.cli._read_raw_records", side_effect=AssertionError("partial collection must not reread JSONL")
            ), patch("base_lp.cli.sha256_file", side_effect=AssertionError("partial collection must not checksum JSONL")):
                self.assertEqual(command_collect(args), 2)

            checkpoint = json.loads((root / "results" / "collection_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["next_block"], 2)
            self.assertEqual(checkpoint["status"], "partial")

            args.max_seconds = 10.0
            with patch("base_lp.cli._client", return_value=fake_rpc), patch(
                "base_lp.cli.timestamp_to_block", side_effect=AssertionError("resume must not resolve timestamps")
            ), patch("base_lp.cli.time.monotonic", return_value=0.0):
                self.assertEqual(command_collect(args), 0)

            self.assertEqual(fake_rpc.log_ranges, [(1, 1), (2, 2), (3, 3)])
            self.assertEqual(fake_rpc.log_topics, [[[SWAP_TOPIC]]] * 3)
            self.assertEqual(
                json.loads((root / "results" / "dataset_manifest.json").read_text(encoding="utf-8"))["status"],
                "success",
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
