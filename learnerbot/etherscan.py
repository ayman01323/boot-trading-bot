from __future__ import annotations

import json
import time
import requests

BASE = "https://api.etherscan.io/v2/api"

class EtherscanV2:
    def __init__(self, api_key: str, chain_id: int = 56, timeout: int = 30):
        if not api_key:
            raise ValueError("ETHERSCAN_API_KEY is not configured")
        self.api_key = api_key
        self.chain_id = chain_id
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, action: str, address: str):
        params = {
            "chainid": str(self.chain_id),
            "module": "account",
            "action": action,
            "address": address,
            "startblock": 0,
            "endblock": 999999999,
            "page": 1,
            "offset": 10000,
            "sort": "desc",
            "apikey": self.api_key,
        }
        r = self.session.get(BASE, params=params, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") == "0" and "No transactions found" not in str(payload.get("message", "")):
            raise RuntimeError(f"Etherscan {action}: {payload.get('message')} {payload.get('result')}")
        return payload

    def normal(self, address: str):
        return self._get("txlist", address)

    def token_transfers(self, address: str):
        return self._get("tokentx", address)

    def internal(self, address: str):
        return self._get("txlistinternal", address)

def cache_wallet(conn, api: EtherscanV2, wallet: str) -> dict:
    results = {}
    for kind, getter in [
        ("normal", api.normal),
        ("token", api.token_transfers),
        ("internal", api.internal),
    ]:
        payload = getter(wallet)
        results[kind] = payload
        conn.execute(
            """INSERT INTO etherscan_cache(wallet,kind,payload_json,fetched_at)
               VALUES(?,?,?,?)
               ON CONFLICT(wallet,kind) DO UPDATE SET
                 payload_json=excluded.payload_json,fetched_at=excluded.fetched_at""",
            (wallet.lower(), kind, json.dumps(payload), int(time.time())),
        )
    conn.commit()
    return results
