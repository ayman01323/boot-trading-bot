from contextlib import closing
from types import SimpleNamespace

from learnerbot import sibot
from learnerbot import sibot_legacy_error_sweep_patch as patch


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=csv_dir)


def _chain(slug="bsc", chain_id=56):
    return SimpleNamespace(slug=slug, chain_id=chain_id)


def _insert(app, chain, wallet, fetched_at, error):
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,history_complete,error
               ) VALUES(?,?,?,?,0,?)""",
            (chain.chain_id, chain.slug, wallet, fetched_at, error),
        )
        conn.commit()


def _clear_error(app, chain, wallet):
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            "UPDATE wallet_history_status SET error='' WHERE chain_id=? AND wallet=?",
            (chain.chain_id, wallet),
        )
        conn.commit()


def test_oldest_legacy_error_is_selected_outside_ranked_queue(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    monkeypatch.setattr(patch, "_sweep_seconds", lambda app, chain: 900)
    _insert(app, chain, "0xbbb", 200, "RuntimeError: ETHERSCAN_API_KEY is not configured")
    _insert(app, chain, "0xaaa", 100, "RuntimeError: ETHERSCAN_API_KEY is not configured")

    assert patch._next_legacy_error_wallet(app, chain, now_epoch=10_000) == "0xaaa"


def test_durable_per_chain_cooldown_blocks_next_old_row(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    monkeypatch.setattr(patch, "_sweep_seconds", lambda app, chain: 900)
    _insert(app, chain, "0xaaa", 100, "ETHERSCAN_API_KEY is not configured")
    _insert(app, chain, "0xbbb", 200, "ETHERSCAN_API_KEY is not configured")

    assert patch._next_legacy_error_wallet(app, chain, now_epoch=10_000) == "0xaaa"
    _clear_error(app, chain, "0xaaa")
    assert patch._next_legacy_error_wallet(app, chain, now_epoch=10_100) is None
    assert patch._next_legacy_error_wallet(app, chain, now_epoch=10_901) == "0xbbb"


def test_cooldown_is_scoped_per_chain(tmp_path, monkeypatch):
    app = _app(tmp_path)
    bsc = _chain("bsc", 56)
    base = _chain("base", 8453)
    monkeypatch.setattr(patch, "_sweep_seconds", lambda app, chain: 900)
    _insert(app, bsc, "0xbsc", 100, "ETHERSCAN_API_KEY is not configured")
    _insert(app, base, "0xbase", 100, "ETHERSCAN_API_KEY is not configured")

    assert patch._next_legacy_error_wallet(app, bsc, now_epoch=10_000) == "0xbsc"
    assert patch._next_legacy_error_wallet(app, base, now_epoch=10_000) == "0xbase"


def test_non_legacy_alchemy_errors_are_not_swept(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    monkeypatch.setattr(patch, "_sweep_seconds", lambda app, chain: 900)
    _insert(app, chain, "0xaaa", 100, "AlchemyHistoryError: HTTP 429")

    assert patch._next_legacy_error_wallet(app, chain, now_epoch=10_000) is None


def test_primary_queue_always_preempts_legacy_sweep(monkeypatch):
    chain = _chain()
    app = SimpleNamespace()
    monkeypatch.setattr(patch, "_PREV_NEXT_HISTORY_WALLET", lambda app, chain: "0xranked")
    called = []
    monkeypatch.setattr(
        patch,
        "_next_legacy_error_wallet",
        lambda app, chain: called.append(True) or "0xlegacy",
    )

    assert patch._next_history_wallet(app, chain) == "0xranked"
    assert called == []


def test_fallback_runs_only_when_primary_queue_is_empty(monkeypatch):
    chain = _chain()
    app = SimpleNamespace()
    monkeypatch.setattr(patch, "_PREV_NEXT_HISTORY_WALLET", lambda app, chain: None)
    monkeypatch.setattr(patch, "_next_legacy_error_wallet", lambda app, chain: "0xlegacy")

    assert patch._next_history_wallet(app, chain) == "0xlegacy"
