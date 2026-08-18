from types import SimpleNamespace

from learnerbot import solana_leader_cursor_reliability_patch as cursor
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _setup(app):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO leaders(telegram_id,rank,wallet,net_profit_sol,win_rate,closed_trades,selected_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("123", 1, "leader", "1", 60.0, 10, 1, 1),
        )
        sol._set_state(conn, "leader_last_signature:leader", "sig1")


def _state(app):
    with sol.connect(app) as conn:
        return sol._state(conn, "leader_last_signature:leader", "")


def test_selected_leader_fast_lane_uses_confirmed_commitment(monkeypatch, tmp_path):
    app = _app(tmp_path)
    calls = []

    def rpc(app, method, params):
        calls.append((method, params))
        return [] if method == "getSignaturesForAddress" else {"transaction": {}}

    monkeypatch.setattr(cursor._sol, "_rpc", rpc)
    cursor._leader_signatures(app, "leader", 20)
    cursor._leader_transaction(app, "sig")

    assert calls[0][0] == "getSignaturesForAddress"
    assert calls[0][1][1]["commitment"] == "confirmed"
    assert calls[1][0] == "getTransaction"
    assert calls[1][1][1]["commitment"] == "confirmed"


def test_transaction_fetch_failure_keeps_cursor_before_failed_signature(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _setup(app)
    monkeypatch.setattr(cursor, "_leader_signatures", lambda *args: [
        {"signature": "sig2", "err": None}, {"signature": "sig1", "err": None}
    ])
    monkeypatch.setattr(
        cursor,
        "_leader_transaction",
        lambda *args: (_ for _ in ()).throw(RuntimeError("rpc timeout")),
    )

    cursor.monitor_leaders_reliable(app)
    assert _state(app) == "sig1"


def test_transient_preflight_reject_keeps_signal_retryable(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _setup(app)
    monkeypatch.setattr(cursor, "_leader_signatures", lambda *args: [
        {"signature": "sig2", "err": None}, {"signature": "sig1", "err": None}
    ])
    monkeypatch.setattr(cursor, "_leader_transaction", lambda *args: {"tx": 1})
    monkeypatch.setattr(cursor._sol, "classify_swap", lambda tx, wallet: {"signature": "sig2", "action": "BUY", "mint": "mint"})
    monkeypatch.setattr(cursor._sol, "_record_leader_event", lambda app, wallet, event: {**event, "leader_wallet": wallet})
    monkeypatch.setattr(cursor._sol, "process_leader_event", lambda app, event: [
        {"telegram_id": "123", "action": "REJECT", "reason": "429 rate limit from Jupiter"}
    ])

    cursor.monitor_leaders_reliable(app)
    assert _state(app) == "sig1"


def test_successfully_processed_signature_advances_cursor(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _setup(app)
    monkeypatch.setattr(cursor, "_leader_signatures", lambda *args: [
        {"signature": "sig2", "err": None}, {"signature": "sig1", "err": None}
    ])
    monkeypatch.setattr(cursor, "_leader_transaction", lambda *args: {"tx": 1})
    monkeypatch.setattr(cursor._sol, "classify_swap", lambda tx, wallet: None)

    cursor.monitor_leaders_reliable(app)
    assert _state(app) == "sig2"


def test_stale_backlog_is_fast_forwarded_when_leader_has_no_open_position(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _setup(app)
    monkeypatch.setattr(cursor.time, "time", lambda: 1000)
    monkeypatch.setattr(cursor._sol, "settings", lambda app: {"max_signal_age_seconds": "30"})
    monkeypatch.setattr(cursor, "_leader_signatures", lambda *args: [
        {"signature": "sig3", "err": None, "blockTime": 990},
        {"signature": "sig2", "err": None, "blockTime": 900},
        {"signature": "sig1", "err": None, "blockTime": 890},
    ])
    fetched = []

    def fetch(app, sig):
        fetched.append(sig)
        return {"tx": 1}

    monkeypatch.setattr(cursor, "_leader_transaction", fetch)
    monkeypatch.setattr(cursor._sol, "classify_swap", lambda tx, wallet: None)

    cursor.monitor_leaders_reliable(app)
    assert fetched == ["sig3"]
    assert _state(app) == "sig3"


def test_stale_signature_is_not_fast_forwarded_when_exit_may_depend_on_it(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _setup(app)
    monkeypatch.setattr(cursor.time, "time", lambda: 1000)
    monkeypatch.setattr(cursor._sol, "settings", lambda app: {"max_signal_age_seconds": "30"})
    monkeypatch.setattr(cursor, "_has_open_position_for_leader", lambda app, wallet: True)
    row = {"signature": "sig2", "err": None, "blockTime": 900}
    assert cursor._can_fast_forward_stale_row(app, "leader", row, {"max_signal_age_seconds": "30"}) is False
