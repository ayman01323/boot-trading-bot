from __future__ import annotations

import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from learnerbot.config import AppSettings, load_chains
from scripts.rpc_health_audit import _http_probe


class _ApexBaseHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/api/v1/rpc/base":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        method = body.get("method")
        if method == "eth_chainId":
            payload = {"jsonrpc": "2.0", "id": body.get("id"), "result": "0x2105"}
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        payload = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32601, "message": "Method not found"},
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A003
        return


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_bot_accepts_apex_base_gateway_csv_and_chain_id(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ApexBaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        gateway_url = f"http://127.0.0.1:{port}/api/v1/rpc/base"

        csv_dir = tmp_path / "CSVbot"
        data_dir = tmp_path / "data"
        csv_dir.mkdir()
        data_dir.mkdir()

        _write_csv(
            csv_dir / "rpc_endpoints.csv",
            ["chain_id", "name", "url", "ws_url", "enabled", "priority"],
            [
                {
                    "chain_id": "8453",
                    "name": "APEX_LOCAL",
                    "url": gateway_url,
                    "ws_url": "",
                    "enabled": "true",
                    "priority": "1",
                }
            ],
        )
        _write_csv(
            csv_dir / "chains.csv",
            [
                "chain_id",
                "slug",
                "name",
                "type",
                "enabled",
                "explorer_url",
                "native_symbol",
                "wrapped_base_symbol",
                "wrapped_base_address",
                "finality_lag_blocks",
                "scan_blocks_per_cycle",
            ],
            [
                {
                    "chain_id": "8453",
                    "slug": "base",
                    "name": "Base",
                    "type": "EVM",
                    "enabled": "true",
                    "explorer_url": "https://basescan.org",
                    "native_symbol": "ETH",
                    "wrapped_base_symbol": "WETH",
                    "wrapped_base_address": "0x0000000000000000000000000000000000000000",
                    "finality_lag_blocks": "3",
                    "scan_blocks_per_cycle": "10",
                }
            ],
        )

        monkeypatch.setenv("CSV_DIR", str(csv_dir))
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        app = AppSettings.load()
        chains = load_chains(app, enabled_only=True)

        assert len(chains) == 1
        assert chains[0].chain_id == 8453
        assert chains[0].slug == "base"
        assert chains[0].rpc_urls == [gateway_url]

        probe = _http_probe(gateway_url, 8453, "EVM", timeout=3.0)
        assert probe["ok"] is True
        assert probe["reported_chain_id"] == 8453
        assert probe["error"] == ""
        assert probe["latency_ms"] >= 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
