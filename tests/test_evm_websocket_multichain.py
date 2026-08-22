from __future__ import annotations

import importlib


def _clear_evm_env(monkeypatch):
    for name in (
        "ALCHEMY_API_KEY",
        "POLYGON_WS_URL", "POLYGON_ALCHEMY_API_KEY",
        "ARBITRUM_WS_URL", "ARBITRUM_ALCHEMY_API_KEY",
        "BNB_WS_URL", "BNB_ALCHEMY_API_KEY", "BSC_WS_URL", "BSC_ALCHEMY_API_KEY",
        "BASE_WS_URL", "BASE_ALCHEMY_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_supported_evm_websocket_chains(monkeypatch):
    _clear_evm_env(monkeypatch)
    mod = importlib.import_module("learnerbot.polygon_websocket_patch")

    monkeypatch.setenv("ALCHEMY_API_KEY", "shared")
    assert mod._chain_ws_url(137) == "wss://polygon-mainnet.g.alchemy.com/v2/shared"
    assert mod._chain_ws_url(42161) == "wss://arb-mainnet.g.alchemy.com/v2/shared"
    assert mod._chain_ws_url(56) == "wss://bnb-mainnet.g.alchemy.com/v2/shared"
    assert mod._chain_ws_url(8453) == "wss://base-mainnet.g.alchemy.com/v2/shared"


def test_explicit_per_chain_websocket_overrides_shared_key(monkeypatch):
    _clear_evm_env(monkeypatch)
    mod = importlib.import_module("learnerbot.polygon_websocket_patch")
    monkeypatch.setenv("ALCHEMY_API_KEY", "shared")
    monkeypatch.setenv("BASE_WS_URL", "wss://example.invalid/base-secret")
    monkeypatch.setenv("BNB_WS_URL", "wss://example.invalid/bnb-secret")

    assert mod._chain_ws_url(8453) == "wss://example.invalid/base-secret"
    assert mod._chain_ws_url(56) == "wss://example.invalid/bnb-secret"


def test_per_chain_key_overrides_shared_key(monkeypatch):
    _clear_evm_env(monkeypatch)
    mod = importlib.import_module("learnerbot.polygon_websocket_patch")
    monkeypatch.setenv("ALCHEMY_API_KEY", "shared")
    monkeypatch.setenv("ARBITRUM_ALCHEMY_API_KEY", "arb-only")

    assert mod._chain_ws_url(42161) == "wss://arb-mainnet.g.alchemy.com/v2/arb-only"
    assert mod._chain_ws_url(8453) == "wss://base-mainnet.g.alchemy.com/v2/shared"
