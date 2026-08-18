from types import SimpleNamespace

from learnerbot import solana_entry_capacity_reconcile_patch as cap
from learnerbot import solana_sibot as sol


class _Store:
    rows = []

    def __init__(self, csv_dir, data_dir=None):
        pass

    def list_wallets(self, tid, enabled_only=False):
        return [dict(r) for r in self.rows]


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _insert_position(app, pid="p1", tid="123", mint="mint"):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, tid, "leader", 1, mint, "LIVE", "OPEN", "100", "0.001", 1, "sig", "0.001", "100", 1,
             "0", "0", 0.0, 0.0, 0, "0", 1),
        )
        conn.commit()


def test_verified_zero_balance_quarantines_stale_position(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_position(app)
    _Store.rows = [
        {"wallet_id": "w1", "address": "ADDR1"},
        {"wallet_id": "w2", "address": "ADDR2"},
    ]
    monkeypatch.setattr(cap, "SolanaWalletStore", _Store)
    monkeypatch.setattr(cap._binding, "_token_balance_for_address", lambda app, address, mint: 0)

    assert cap._verified_open_live_count(app, "123") == 0
    with sol.connect(app) as conn:
        row = conn.execute("SELECT status,exit_reason FROM positions WHERE position_id='p1'").fetchone()
    assert row["status"] == "RECONCILE_REQUIRED"
    assert "verified zero balance" in row["exit_reason"]


def test_rpc_uncertainty_keeps_position_counted(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_position(app)
    _Store.rows = [{"wallet_id": "w1", "address": "ADDR1"}]
    monkeypatch.setattr(cap, "SolanaWalletStore", _Store)

    def fail(*args, **kwargs):
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(cap._binding, "_token_balance_for_address", fail)
    assert cap._verified_open_live_count(app, "123") == 1
    with sol.connect(app) as conn:
        row = conn.execute("SELECT status FROM positions WHERE position_id='p1'").fetchone()
    assert row["status"] == "OPEN"


def test_real_token_balance_keeps_position_counted(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_position(app)
    _Store.rows = [{"wallet_id": "w1", "address": "ADDR1"}]
    monkeypatch.setattr(cap, "SolanaWalletStore", _Store)
    monkeypatch.setattr(cap._binding, "_token_balance_for_address", lambda app, address, mint: 50)
    assert cap._verified_open_live_count(app, "123") == 1
