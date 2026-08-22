import unittest
from unittest.mock import patch

import requests

from base_lp.data.rpc import JsonRpcClient


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return FakeResponse()


class RpcClientTests(unittest.TestCase):
    def test_request_builds_json_rpc_payload(self):
        client = JsonRpcClient("https://example.invalid")
        session = FakeSession()
        client.session = session

        result = client.request("eth_chainId", [])

        self.assertEqual(result, "0x1")
        self.assertEqual(session.calls[0][1]["method"], "eth_chainId")
        self.assertEqual(session.calls[0][1]["id"], 1)

    def test_latest_block_wrapper_returns_integer(self):
        client = JsonRpcClient("https://example.invalid")
        client.session = FakeSession()

        self.assertEqual(client.latest_block(), 1)

    def test_429_retry_honors_retry_after_header(self):
        class RateLimitedResponse(FakeResponse):
            status_code = 429
            headers = {"Retry-After": "0"}

            def raise_for_status(self):
                raise requests.HTTPError(response=self)

        class SequenceSession:
            def __init__(self):
                self.responses = [RateLimitedResponse(), FakeResponse()]

            def post(self, url, json, timeout):
                return self.responses.pop(0)

        client = JsonRpcClient("https://example.invalid", max_retries=1)
        client.session = SequenceSession()
        with patch("base_lp.data.rpc.time.sleep") as sleep:
            self.assertEqual(client.request("eth_chainId", []), "0x1")

        sleep.assert_called_once_with(0.0)


if __name__ == "__main__":
    unittest.main()
