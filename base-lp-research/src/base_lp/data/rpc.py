from __future__ import annotations

import itertools
import time
from typing import Any

import requests


def hex_to_int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"expected hex quantity, got {value!r}")
    return int(value[2:] or "0", 16)


def normalize_address(value: str) -> str:
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        raise ValueError(f"invalid EVM address: {value!r}")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise ValueError(f"invalid EVM address: {value!r}") from exc
    return value.lower()


class JsonRpcError(RuntimeError):
    pass


class JsonRpcClient:
    def __init__(self, url: str, timeout_seconds: float = 30.0, max_retries: int = 3) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("RPC URL must use http:// or https://")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._ids = itertools.count(1)
        self.session = requests.Session()

    def request(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or []}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.url, json=payload, timeout=self.timeout_seconds)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                body = response.json()
                if "error" in body:
                    raise JsonRpcError(f"{method}: {body['error']}")
                return body["result"]
            except (requests.RequestException, ValueError, JsonRpcError) as exc:
                last_error = exc
                if isinstance(exc, JsonRpcError):
                    raise
                if attempt >= self.max_retries:
                    break
                delay = min(2**attempt, 8)
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    retry_after = exc.response.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            delay = max(0.0, float(retry_after))
                        except ValueError:
                            pass
                time.sleep(delay)
        raise JsonRpcError(f"RPC request failed after retries: {method}") from last_error

    def batch_request(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        if not calls:
            return []
        ids = [next(self._ids) for _ in calls]
        payload = [
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            for request_id, (method, params) in zip(ids, calls)
        ]
        response = self.session.post(self.url, json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()
        bodies = response.json()
        by_id = {body["id"]: body for body in bodies}
        results: list[Any] = []
        for request_id, (method, _) in zip(ids, calls):
            body = by_id.get(request_id)
            if body is None:
                raise JsonRpcError(f"batch response missing id {request_id}")
            if "error" in body:
                raise JsonRpcError(f"{method}: {body['error']}")
            results.append(body["result"])
        return results

    def latest_block(self) -> int:
        return hex_to_int(str(self.request("eth_blockNumber")))

    def block_by_number(self, block_number: int, full_transactions: bool = False) -> dict[str, Any] | None:
        result = self.request("eth_getBlockByNumber", [hex(block_number), full_transactions])
        return result if result is None else dict(result)

    def logs(self, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
        result = self.request(
            "eth_getLogs",
            [
                {
                    "address": normalize_address(address),
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                }
            ],
        )
        return [dict(item) for item in result]

    def receipt(self, transaction_hash: str) -> dict[str, Any] | None:
        result = self.request("eth_getTransactionReceipt", [transaction_hash])
        return result if result is None else dict(result)
