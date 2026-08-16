from __future__ import annotations
from typing import Optional

import itertools
import time
from typing import Any
import requests

class RPCError(RuntimeError):
    pass

class RPCClient:
    def __init__(self, urls: list[str], timeout: float = 15, delay_ms: int = 35):
        if not urls:
            raise ValueError("At least one EVM RPC URL is required")
        self.urls = urls
        self.timeout = timeout
        self.delay = max(0, delay_ms) / 1000.0
        self._counter = itertools.count(1)
        self._url_index = 0
        self.session = requests.Session()

    def _next_urls(self):
        for i in range(len(self.urls)):
            yield self.urls[(self._url_index + i) % len(self.urls)]

    def call(self, method: str, params: list[Any]) -> Any:
        last_error = None
        for url in self._next_urls():
            if self.delay:
                time.sleep(self.delay)
            payload = {
                "jsonrpc": "2.0",
                "id": next(self._counter),
                "method": method,
                "params": params,
            }
            try:
                r = self.session.post(url, json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise RPCError(f"{method}: {data['error']}")
                self._url_index = self.urls.index(url)
                return data.get("result")
            except Exception as exc:
                last_error = exc
                continue
        raise RPCError(f"All RPC endpoints failed for {method}: {last_error}")

    def latest_block(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block(self, number: int, full_transactions: bool = True) -> dict:
        result = self.call("eth_getBlockByNumber", [hex(number), full_transactions])
        if not result:
            raise RPCError(f"Block {number} not found")
        return result

    def receipt(self, tx_hash: str) -> Optional[dict]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def code(self, address: str, block: str = "latest") -> str:
        return self.call("eth_getCode", [address, block]) or "0x"

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block]) or "0x"
