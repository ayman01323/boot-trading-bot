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


def test_legacy_sweep_wins_when_its_cooldown_is_due(monkeypatch):
    # The cooldown inside _next_legacy_error_wallet is what bounds how often
    # this can preempt a ranked candidate, not queue idleness -- so when the
    # sweep is due, it must win even though the ranked queue has something.
    chain = _chain()
    app = SimpleNamespace()
    monkeypatch.setattr(patch, "_PREV_NEXT_HISTORY_WALLET", lambda app, chain: "0xranked")
    monkeypatch.setattr(patch, "_next_legacy_error_wallet", lambda app, chain: "0xlegacy")

    assert patch._next_history_wallet(app, chain) == "0xlegacy"


def test_ranked_queue_used_when_legacy_sweep_not_due(monkeypatch):
    # On every pass within the cooldown window, _next_legacy_error_wallet
    # returns None immediately (cheap, no wallet claimed) and the ranked
    # queue proceeds completely unaffected -- this is the common case.
    chain = _chain()
    app = SimpleNamespace()
    monkeypatch.setattr(patch, "_PREV_NEXT_HISTORY_WALLET", lambda app, chain: "0xranked")
    monkeypatch.setattr(patch, "_next_legacy_error_wallet", lambda app, chain: None)

    assert patch._next_history_wallet(app, chain) == "0xranked"


def test_legacy_sweep_is_not_starved_by_a_ranked_queue_that_never_goes_idle(tmp_path, monkeypatch):
    # Regression test for the confirmed live bug: a ranked/progress queue that
    # always finds *something* to retry (e.g. a large, actively-refreshing
    # candidate window) must not permanently prevent the legacy backlog from
    # ever being swept. Simulate the ranked queue always returning a wallet
    # -- never idle -- across many passes, and confirm the sweep still fires
    # once its cooldown elapses, exercising the real call path
    # _history_worker uses (no now_epoch override).
    app = _app(tmp_path)
    chain = _chain()
    monkeypatch.setattr(patch, "_sweep_seconds", lambda app, chain: 900)
    monkeypatch.setattr(patch, "_PREV_NEXT_HISTORY_WALLET", lambda app, chain: "0xranked")
    _insert(app, chain, "0xstale", 100, "ETHERSCAN_API_KEY is not configured")

    # First-ever call has no recorded cooldown state, so it sweeps
    # immediately (correct: don't wait 15 minutes after a fresh install
    # before the very first sweep). This consumes the cooldown for t=100.
    monkeypatch.setattr(patch.time, "time", lambda: 100)
    assert patch._next_history_wallet(app, chain) == "0xstale"

    # Many passes within that cooldown window: the never-idle ranked queue
    # wins every time, exactly matching normal (non-bursty) pacing.
    for now in (150, 500, 999):
        monkeypatch.setattr(patch.time, "time", lambda now=now: now)
        assert patch._next_history_wallet(app, chain) == "0xranked"

    # Once the cooldown elapses, the sweep activates again despite the
    # ranked queue still always having a candidate -- proving it is not
    # permanently starved by a queue that never returns None.
    monkeypatch.setattr(patch.time, "time", lambda: 1_001)
    assert patch._next_history_wallet(app, chain) == "0xstale"
