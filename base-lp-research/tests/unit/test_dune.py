import unittest

from base_lp.data.events import SWAP_TOPIC
from base_lp.data.dune import DuneClient, build_swap_logs_sql, dune_row_to_log_record


POOL = "0xd0b53d9277642d899df5c87a3966a349a798f224"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.post_calls = []
        self.result_params = []
        self.statuses = [
            {"state": "QUERY_STATE_PENDING"},
            {"state": "QUERY_STATE_COMPLETED"},
        ]
        self.pages = [
            {
                "result": {
                    "metadata": {"total_row_count": 3},
                    "rows": [{"id": 1}, {"id": 2}],
                }
            },
            {
                "result": {
                    "metadata": {"total_row_count": 3},
                    "rows": [{"id": 3}],
                }
            },
        ]

    def post(self, url, json, headers, timeout):
        self.post_calls.append((url, json, headers, timeout))
        return FakeResponse({"execution_id": "execution-1", "state": "QUERY_STATE_PENDING"})

    def get(self, url, headers, params=None, timeout=None):
        if url.endswith("/status"):
            return FakeResponse(self.statuses.pop(0))
        self.result_params.append(params)
        return FakeResponse(self.pages.pop(0))


class DuneTests(unittest.TestCase):
    def test_sql_limits_the_single_pool_to_swaps_and_requested_half_open_window(self):
        sql = build_swap_logs_sql(POOL, "2026-06-01T00:00:00Z", "2026-07-01T00:00:00Z")

        self.assertIn("FROM base.logs", sql)
        self.assertIn(f"contract_address = {POOL}", sql)
        self.assertIn(f"topic0 = {SWAP_TOPIC}", sql)
        self.assertIn("block_time >= TIMESTAMP '2026-06-01 00:00:00'", sql)
        self.assertIn("block_time < TIMESTAMP '2026-07-01 00:00:00'", sql)
        self.assertIn('ORDER BY block_number, tx_index, "index"', sql)

    def test_row_conversion_preserves_log_order_topics_and_utc_timestamp(self):
        row = {
            "block_time": "2026-06-01 08:11:29.000 UTC",
            "block_number": 46756071,
            "block_hash": "0xblock",
            "contract_address": POOL,
            "topic0": SWAP_TOPIC,
            "topic1": "0x" + "00" * 32,
            "topic2": "0x" + "11" * 32,
            "topic3": None,
            "data": "0x" + "22" * 160,
            "tx_hash": "0xtx",
            "log_index": 7,
            "tx_index": 3,
        }

        record = dune_row_to_log_record(row)

        self.assertEqual(record.stable_key, (46756071, 3, 7))
        self.assertEqual(record.topics, [SWAP_TOPIC, "0x" + "00" * 32, "0x" + "11" * 32])
        self.assertEqual(record.timestamp, 1780301489)

    def test_client_executes_once_then_pages_completed_results_without_reexecution(self):
        session = FakeSession()
        client = DuneClient("test-key", session=session, timeout_seconds=5)

        execution_id = client.execute_sql("SELECT 1")
        client.wait_for_completion(execution_id, poll_interval_seconds=0)
        rows = list(
            client.iter_result_rows(
                execution_id,
                page_size=2,
                columns=["block_time", "block_number", "topic1"],
            )
        )

        self.assertEqual(execution_id, "execution-1")
        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(len(session.post_calls), 1)
        self.assertEqual(session.post_calls[0][1], {"sql": "SELECT 1"})
        self.assertEqual(session.post_calls[0][2]["X-Dune-API-Key"], "test-key")
        self.assertEqual(
            session.result_params,
            [
                {"offset": 0, "limit": 2, "columns": "block_time,block_number,topic1"},
                {"offset": 2, "limit": 2, "columns": "block_time,block_number,topic1"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
