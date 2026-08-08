import unittest

from base_lp.data.rpc import JsonRpcClient


class BatchResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return [
            {"jsonrpc": "2.0", "id": 2, "result": "0x2"},
            {"jsonrpc": "2.0", "id": 1, "result": "0x1"},
        ]


class BatchSession:
    def post(self, url, json, timeout):
        self.payload = json
        return BatchResponse()


class RpcBatchTests(unittest.TestCase):
    def test_batch_request_returns_results_in_input_order(self):
        client = JsonRpcClient("https://example.invalid")
        client.session = BatchSession()

        results = client.batch_request([("eth_blockNumber", []), ("eth_chainId", [])])

        self.assertEqual(results, ["0x1", "0x2"])


if __name__ == "__main__":
    unittest.main()
