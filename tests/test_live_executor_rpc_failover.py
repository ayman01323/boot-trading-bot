from __future__ import annotations

from learnerbot.config import ChainConfig
from learnerbot import live_executor_rpc_failover_patch as patch


def _chain(urls):
    return ChainConfig(
        chain_id=56,
        slug="bsc",
        name="BNB Smart Chain",
        type="EVM",
        enabled=True,
        explorer_url="",
        native_symbol="BNB",
        wrapped_base_symbol="WBNB",
        wrapped_base_address="0x0000000000000000000000000000000000000001",
        finality_lag_blocks=3,
        scan_blocks_per_cycle=10,
        rpc_urls=list(urls),
    )


def test_moves_first_reachable_correct_chain_rpc_to_front(monkeypatch):
    urls = ["https://dead.example/key-a", "https://good.example/key-b", "https://later.example/key-c"]
    patch._CACHE.clear()
    seen = []

    def probe(url, chain_id):
        seen.append((url, chain_id))
        return url == urls[1]

    monkeypatch.setattr(patch, "_probe", probe)
    assert patch._ordered_rpc_urls(_chain(urls)) == [urls[1], urls[0], urls[2]]
    assert seen == [(urls[0], 56), (urls[1], 56)]


def test_keeps_configured_priority_when_first_rpc_is_healthy(monkeypatch):
    urls = ["https://primary.example/key-a", "https://fallback.example/key-b"]
    patch._CACHE.clear()
    seen = []

    def probe(url, chain_id):
        seen.append(url)
        return True

    monkeypatch.setattr(patch, "_probe", probe)
    assert patch._ordered_rpc_urls(_chain(urls)) == urls
    assert seen == [urls[0]]


def test_all_failed_endpoints_remain_fail_closed(monkeypatch):
    urls = ["https://dead-a.example/key-a", "https://dead-b.example/key-b"]
    patch._CACHE.clear()
    monkeypatch.setattr(patch, "_probe", lambda url, chain_id: False)
    assert patch._ordered_rpc_urls(_chain(urls)) == urls


def test_placeholder_and_non_http_rpc_are_never_probed_as_valid():
    assert patch._valid_http_url("https://bnb-mainnet.g.alchemy.com/v2/real") is True
    assert patch._valid_http_url("https://bnb-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY}") is False
    assert patch._valid_http_url("wss://bnb-mainnet.g.alchemy.com/v2/real") is False


def test_load_chains_reorders_only_rpc_urls(monkeypatch):
    original = _chain(["https://dead.example/a", "https://good.example/b"])
    patch._CACHE.clear()
    monkeypatch.setattr(patch, "_ORIGINAL_LOAD_CHAINS", lambda app, enabled_only=False: [original])
    monkeypatch.setattr(patch, "_probe", lambda url, chain_id: "good.example" in url)
    got = patch._load_chains_with_execution_failover(object(), enabled_only=False)[0]
    assert got.chain_id == 56
    assert got.slug == original.slug
    assert got.enabled is True
    assert got.rpc_urls == ["https://good.example/b", "https://dead.example/a"]
