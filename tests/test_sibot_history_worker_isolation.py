from types import SimpleNamespace

import pytest

from learnerbot import sibot


class _StopLoop(Exception):
    """Sentinel used to escape sibot._history_worker's `while True` after one pass."""


def _install_common_mocks(monkeypatch, chains):
    monkeypatch.setattr(sibot, "ensure_settings", lambda app: None)
    monkeypatch.setattr(
        sibot, "platform_settings",
        lambda app, chain_id=0: {"platform_enabled": "true", "history_worker_seconds": "12"},
    )
    monkeypatch.setattr(sibot, "load_chains", lambda app, enabled_only=True: chains)

    def fake_sleep(seconds):
        raise _StopLoop()

    monkeypatch.setattr(sibot.time, "sleep", fake_sleep)


def test_history_worker_isolates_a_failing_chain_from_the_rest(monkeypatch):
    chains = [SimpleNamespace(slug="bsc"), SimpleNamespace(slug="base"), SimpleNamespace(slug="ethereum")]
    attempted = []

    def fake_next_history_wallet(app, chain):
        attempted.append(chain.slug)
        if chain.slug == "base":
            raise RuntimeError("boom")
        return f"wallet-{chain.slug}"

    refreshed = []
    monkeypatch.setattr(sibot, "_next_history_wallet", fake_next_history_wallet)
    monkeypatch.setattr(sibot, "refresh_wallet_history", lambda app, chain, wallet: refreshed.append((chain.slug, wallet)))
    ranked = []
    monkeypatch.setattr(sibot, "refresh_all_rankings", lambda app: ranked.append(True))
    _install_common_mocks(monkeypatch, chains)

    with pytest.raises(_StopLoop):
        sibot._history_worker(SimpleNamespace())

    # All three chains must be attempted in the same pass even though the
    # middle one raises -- one chain's failure must not starve every chain
    # that comes after it in iteration order for the rest of that pass.
    assert attempted == ["bsc", "base", "ethereum"]
    assert refreshed == [("bsc", "wallet-bsc"), ("ethereum", "wallet-ethereum")]
    # Ranking still runs for the pass even though one chain errored.
    assert ranked == [True]


def test_history_worker_continues_after_refresh_raises(monkeypatch):
    chains = [SimpleNamespace(slug="bsc"), SimpleNamespace(slug="polygon")]
    monkeypatch.setattr(sibot, "_next_history_wallet", lambda app, chain: f"wallet-{chain.slug}")

    refreshed = []

    def fake_refresh(app, chain, wallet):
        if chain.slug == "bsc":
            raise RuntimeError("rpc timeout")
        refreshed.append((chain.slug, wallet))

    monkeypatch.setattr(sibot, "refresh_wallet_history", fake_refresh)
    monkeypatch.setattr(sibot, "refresh_all_rankings", lambda app: None)
    _install_common_mocks(monkeypatch, chains)

    with pytest.raises(_StopLoop):
        sibot._history_worker(SimpleNamespace())

    assert refreshed == [("polygon", "wallet-polygon")]
