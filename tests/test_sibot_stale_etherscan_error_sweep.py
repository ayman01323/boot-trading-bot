import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from learnerbot import sibot


class _StopLoop(Exception):
    """Sentinel used to escape sibot._history_worker's `while True` after one pass."""


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def _chain(slug="bsc", chain_id=56):
    return SimpleNamespace(slug=slug, chain_id=chain_id)


def _insert_status(app, chain, wallet, *, fetched_at, error):
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            "INSERT INTO wallet_history_status(chain_id,chain_slug,wallet,fetched_at,history_complete,error)"
            " VALUES(?,?,?,?,0,?)",
            (chain.chain_id, chain.slug, wallet, fetched_at, error),
        )
        conn.commit()


def test_next_stale_etherscan_error_wallet_picks_oldest_legacy_error(tmp_path):
    app = _app(tmp_path)
    chain = _chain()
    old_enough = int(time.time()) - 2 * sibot._STALE_ETHERSCAN_ERROR_RETRY_SECONDS
    _insert_status(app, chain, "0xaaa", fetched_at=old_enough + 100, error="RuntimeError: ETHERSCAN_API_KEY is not configured")
    _insert_status(app, chain, "0xbbb", fetched_at=old_enough, error="RuntimeError: ETHERSCAN_API_KEY is not configured")

    picked = sibot._next_stale_etherscan_error_wallet(app, chain)

    assert picked == "0xbbb"


def test_next_stale_etherscan_error_wallet_respects_cooldown(tmp_path):
    app = _app(tmp_path)
    chain = _chain()
    _insert_status(app, chain, "0xaaa", fetched_at=int(time.time()), error="RuntimeError: ETHERSCAN_API_KEY is not configured")

    assert sibot._next_stale_etherscan_error_wallet(app, chain) is None


def test_next_stale_etherscan_error_wallet_ignores_non_legacy_errors(tmp_path):
    app = _app(tmp_path)
    chain = _chain()
    old_enough = int(time.time()) - 2 * sibot._STALE_ETHERSCAN_ERROR_RETRY_SECONDS
    _insert_status(app, chain, "0xaaa", fetched_at=old_enough, error="AlchemyHistoryError: RateLimitError: 429")

    assert sibot._next_stale_etherscan_error_wallet(app, chain) is None


def test_next_stale_etherscan_error_wallet_ignores_other_chains(tmp_path):
    app = _app(tmp_path)
    old_enough = int(time.time()) - 2 * sibot._STALE_ETHERSCAN_ERROR_RETRY_SECONDS
    _insert_status(app, _chain(slug="base", chain_id=8453), "0xaaa", fetched_at=old_enough, error="RuntimeError: ETHERSCAN_API_KEY is not configured")

    assert sibot._next_stale_etherscan_error_wallet(app, _chain(slug="bsc", chain_id=56)) is None


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


def test_history_worker_falls_back_to_stale_error_wallet_when_no_ranked_candidate(monkeypatch):
    chains = [_chain()]
    monkeypatch.setattr(sibot, "_next_history_wallet", lambda app, chain: None)
    monkeypatch.setattr(sibot, "_next_stale_etherscan_error_wallet", lambda app, chain: "0xstale")

    refreshed = []
    monkeypatch.setattr(sibot, "refresh_wallet_history", lambda app, chain, wallet: refreshed.append(wallet))
    monkeypatch.setattr(sibot, "refresh_all_rankings", lambda app: None)
    _install_common_mocks(monkeypatch, chains)

    with pytest.raises(_StopLoop):
        sibot._history_worker(SimpleNamespace())

    assert refreshed == ["0xstale"]


def test_history_worker_prefers_ranked_candidate_over_stale_sweep(monkeypatch):
    chains = [_chain()]
    monkeypatch.setattr(sibot, "_next_history_wallet", lambda app, chain: "0xranked")
    stale_calls = []
    monkeypatch.setattr(sibot, "_next_stale_etherscan_error_wallet", lambda app, chain: stale_calls.append(1) or "0xstale")

    refreshed = []
    monkeypatch.setattr(sibot, "refresh_wallet_history", lambda app, chain, wallet: refreshed.append(wallet))
    monkeypatch.setattr(sibot, "refresh_all_rankings", lambda app: None)
    _install_common_mocks(monkeypatch, chains)

    with pytest.raises(_StopLoop):
        sibot._history_worker(SimpleNamespace())

    assert refreshed == ["0xranked"]
    assert stale_calls == []
