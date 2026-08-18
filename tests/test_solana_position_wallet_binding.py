from types import SimpleNamespace

import pytest

from learnerbot import solana_position_wallet_binding_patch as bind
from learnerbot import solana_sibot as sol


class _Store:
    rows = []

    def __init__(self, csv_dir, data_dir=None):
        pass

    def list_wallets(self, tid, enabled_only=True):
        rows = [dict(r) for r in self.rows]
        if enabled_only:
            rows = [r for r in rows if str(r.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}]
        return rows

    def get_meta(self, tid, wallet_id=None):
        rows = self.list_wallets(tid, enabled_only=True)
        if wallet_id:
            for row in rows:
                if row.get("wallet_id") == wallet_id:
                    return row
            raise RuntimeError("wallet not found")
        for row in rows:
            if str(row.get("active") or "").lower() == "true":
                return row
        return rows[0]

    def has_private_key(self, tid, wallet_id=None):
        try:
            row = self.get_meta(tid, wallet_id)
        except Exception:
            return False
        return str(row.get("signing") or "").lower() == "true"

    def keypair_bytes(self, tid, wallet_id=None):
        return b"k" * 64


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


def test_executor_can_pin_specific_wallet_even_if_other_wallet_is_active(monkeypatch, tmp_path):
    _Store.rows = [
        {"wallet_id": "entry", "address": "ENTRYADDR", "signing": "true", "enabled": "true", "active": "false"},
        {"wallet_id": "newactive", "address": "ACTIVEADDR", "signing": "true", "enabled": "true", "active": "true"},
    ]
    monkeypatch.setattr(bind, "SolanaWalletStore", _Store)
    ex = bind._exec.SolanaLiveExecutor(_app(tmp_path), "123", wallet_id="entry")
    assert ex.wallet_id == "entry"
    assert ex.address == "ENTRYADDR"


def test_legacy_position_resolves_wallet_that_actually_holds_token(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_position(app)
    _Store.rows = [
        {"wallet_id": "entry", "address": "ENTRYADDR", "signing": "true", "enabled": "true", "active": "false"},
        {"wallet_id": "newactive", "address": "ACTIVEADDR", "signing": "true", "enabled": "true", "active": "true"},
    ]
    monkeypatch.setattr(bind, "SolanaWalletStore", _Store)
    monkeypatch.setattr(bind, "_token_balance_for_address", lambda app, address, mint: 100 if address == "ENTRYADDR" else 0)
    with sol.connect(app) as conn:
        p = dict(conn.execute("SELECT * FROM positions WHERE position_id='p1'").fetchone())
    ex, bal = bind._resolve_executor(app, "123", p)
    assert ex.wallet_id == "entry"
    assert bal == 100
    row = bind._binding(app, "p1")
    assert row["wallet_id"] == "entry"
    assert row["source"] == "LEGACY_RESOLVED"


def test_no_registered_wallet_token_quarantines_position_once(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_position(app)
    _Store.rows = [
        {"wallet_id": "w1", "address": "ADDR1", "signing": "true", "enabled": "true", "active": "true"},
        {"wallet_id": "w2", "address": "ADDR2", "signing": "true", "enabled": "true", "active": "false"},
    ]
    monkeypatch.setattr(bind, "SolanaWalletStore", _Store)
    monkeypatch.setattr(bind, "_token_balance_for_address", lambda app, address, mint: 0)
    with sol.connect(app) as conn:
        p = dict(conn.execute("SELECT * FROM positions WHERE position_id='p1'").fetchone())
    with pytest.raises(bind.SolanaPositionReconcileRequired, match="RECONCILE_REQUIRED"):
        bind._resolve_executor(app, "123", p)
    with sol.connect(app) as conn:
        row = conn.execute("SELECT status,exit_reason FROM positions WHERE position_id='p1'").fetchone()
    assert row["status"] == "RECONCILE_REQUIRED"
    assert "none of the user's registered Solana wallets" in row["exit_reason"]


def test_new_position_records_entry_wallet_binding(monkeypatch, tmp_path):
    app = _app(tmp_path)
    monkeypatch.setattr(bind, "_PREV_INSERT", lambda *a, **k: ("p-new", 500, sol._dec("0.001")))
    pid, out_raw, cost = bind._insert_with_wallet_binding(
        app, "123", 1,
        {"leader_wallet": "leader", "mint": "mint", "signature": "sig", "sol_amount": "0.001", "token_amount_raw": "500"},
        {"wallet_id": "entry", "wallet_address": "ENTRYADDR"}, sol._dec("0.001"), {},
    )
    assert pid == "p-new" and out_raw == 500
    row = bind._binding(app, "p-new")
    assert row["wallet_id"] == "entry"
    assert row["wallet_address"] == "ENTRYADDR"
    assert row["source"] == "ENTRY"
