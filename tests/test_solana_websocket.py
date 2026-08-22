from __future__ import annotations

import importlib


def _clear_env(monkeypatch):
    for name in ("SOLANA_WS_URL", "HELIUS_WS_URL", "HELIUS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_rpc_to_ws_conversion():
    mod = importlib.import_module("learnerbot.solana_websocket_patch")
    assert mod._rpc_to_ws("https://mainnet.helius-rpc.com/?api-key=x") == (
        "wss://mainnet.helius-rpc.com/?api-key=x"
    )
    assert mod._rpc_to_ws("http://127.0.0.1:8899") == "ws://127.0.0.1:8899"
    assert mod._rpc_to_ws("wss://example.invalid/ws") == "wss://example.invalid/ws"


def test_explicit_solana_ws_url_wins(monkeypatch):
    _clear_env(monkeypatch)
    mod = importlib.import_module("learnerbot.solana_websocket_patch")
    monkeypatch.setenv("HELIUS_API_KEY", "shared")
    monkeypatch.setenv("SOLANA_WS_URL", "wss://example.invalid/solana-secret")

    assert mod._solana_ws_url(object()) == "wss://example.invalid/solana-secret"


def test_helius_key_builds_standard_wss(monkeypatch):
    _clear_env(monkeypatch)
    mod = importlib.import_module("learnerbot.solana_websocket_patch")
    monkeypatch.setenv("HELIUS_API_KEY", "helius-key")

    assert mod._solana_ws_url(object()) == (
        "wss://mainnet.helius-rpc.com/?api-key=helius-key"
    )
