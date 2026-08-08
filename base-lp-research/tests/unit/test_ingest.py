import unittest
from unittest.mock import patch

from eth_hash.auto import keccak

from base_lp.data.events import SWAP_TOPIC
from base_lp.data.ingest import collect_logs, iter_log_chunks, normalize_event_rows


def word(value: int) -> str:
    return value.to_bytes(32, "big", signed=value < 0).hex() if value >= 0 else ((1 << 256) + value).to_bytes(32, "big").hex()


class FakeRpc:
    def request(self, method, params):
        if method == "eth_getLogs":
            return [
                {
                    "blockNumber": "0xa",
                    "transactionIndex": "0x0",
                    "logIndex": "0x0",
                    "blockHash": "0xblock",
                    "transactionHash": "0xtx",
                    "address": "0x" + "ab" * 20,
                    "topics": [SWAP_TOPIC, "0x" + "00" * 32, "0x" + "00" * 32],
                    "data": "0x" + "".join([word(-10), word(20), word(2**96), word(1000), word(0)]),
                }
            ]
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x64"}
        raise AssertionError(method)


class IngestTests(unittest.TestCase):
    def test_collect_logs_injects_block_timestamp(self):
        records = collect_logs(FakeRpc(), "0x" + "ab" * 20, 10, 11, chunk_size=2)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].timestamp, 100)
        self.assertEqual(records[0].block_number, 10)

    def test_normalize_event_rows_keeps_swap_fields(self):
        records = collect_logs(FakeRpc(), "0x" + "ab" * 20, 10, 11, chunk_size=2)

        rows = normalize_event_rows(records)

        self.assertEqual(rows[0]["event_type"], "swap")
        self.assertEqual(rows[0]["amount0"], -10)
        self.assertEqual(rows[0]["tick"], 0)

    def test_iter_log_chunks_is_bounded_and_paces_repeated_rpc_calls(self):
        class ChunkedRpc:
            def __init__(self):
                self.log_ranges = []

            def request(self, method, params):
                if method == "eth_getLogs":
                    query = params[0]
                    start = int(query["fromBlock"], 16)
                    end = int(query["toBlock"], 16)
                    self.log_ranges.append((start, end))
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
                    block = int(params[0], 16)
                    return {"timestamp": hex(block + 100)}
                raise AssertionError(method)

        rpc = ChunkedRpc()
        with patch("base_lp.data.ingest.time.sleep") as sleep:
            chunks = list(
                iter_log_chunks(
                    rpc,
                    "0x" + "ab" * 20,
                    10,
                    13,
                    chunk_size=2,
                    request_interval_seconds=0.25,
                )
            )

        self.assertEqual(rpc.log_ranges, [(10, 11), (12, 13)])
        self.assertEqual([len(chunk) for chunk in chunks], [1, 1])
        self.assertGreaterEqual(sleep.call_count, 2)
        self.assertTrue(all(call.args == (0.25,) for call in sleep.call_args_list))


if __name__ == "__main__":
    unittest.main()
