from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_refundable_rent_accounting_patch as accounting
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _insert_open(app, cost="0.0023444"):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("p1", "123", "leader", 1, "mint", "LIVE", "OPEN", "100", cost, 1, "buy", "0.0005", "100", 1,
             "0", "0", 0.0, 0.0, 0, "0", 1),
        )
        conn.commit()


def test_open_valuation_excludes_refundable_rent(monkeypatch):
    seen = {}
    monkeypatch.setattr(accounting, "_rent_principal_sol", lambda app, pid: Decimal("0.0018444"))

    def previous(app, position, fraction):
        seen["cost"] = Decimal(position["entry_cost_sol"])
        return {"net_sol": Decimal("0"), "net_pct": Decimal("0")}

    monkeypatch.setattr(accounting, "_PREV_EVALUATE", previous)
    result = accounting.evaluate_position_economic(
        object(),
        {"position_id": "p1", "mode": "LIVE", "entry_cost_sol": "0.0023444"},
        Decimal(1),
    )
    assert seen["cost"] == Decimal("0.0005000")
    assert result["refundable_rent_sol"] == Decimal("0.0018444")


def test_partial_sell_keeps_full_rent_with_remaining_position(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_open(app)
    with sol.connect(app) as conn:
        position = dict(conn.execute("SELECT * FROM positions WHERE position_id='p1'").fetchone())

    class Executor:
        address = "ABCDEFGH1234567890"
        def sell(self, mint, raw):
            assert raw == 50
            return {"signature": "sell", "wallet_delta_lamports": 300_000, "totalOutputAmount": "300000"}

    monkeypatch.setattr(accounting._binding, "_resolve_executor", lambda *args: (Executor(), 100))
    monkeypatch.setattr(accounting, "_rent_principal_sol", lambda app, pid: Decimal("0.0018444"))
    called = []
    monkeypatch.setattr(accounting._reclaim, "_close_created_empty_accounts", lambda *args: called.append(True))
    monkeypatch.setattr(accounting._live, "_notify", lambda *args: None)

    result = accounting._close_live_rent_aware(app, "123", position, Decimal("0.5"), "PARTIAL")
    assert result["closed"] is False
    assert result["net_sol"] == Decimal("0.00005000")
    assert called == []
    with sol.connect(app) as conn:
        row = conn.execute("SELECT token_amount_raw,entry_cost_sol FROM positions WHERE position_id='p1'").fetchone()
    assert int(row["token_amount_raw"]) == 50
    assert Decimal(row["entry_cost_sol"]) == Decimal("0.00209440")


def test_full_sell_cash_reconciles_rent_exactly_once(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _insert_open(app)
    with sol.connect(app) as conn:
        position = dict(conn.execute("SELECT * FROM positions WHERE position_id='p1'").fetchone())

    class Executor:
        address = "ABCDEFGH1234567890"
        def sell(self, mint, raw):
            return {"signature": "sell", "wallet_delta_lamports": 500_000, "totalOutputAmount": "500000"}

    monkeypatch.setattr(accounting._binding, "_resolve_executor", lambda *args: (Executor(), 100))
    monkeypatch.setattr(accounting, "_rent_principal_sol", lambda app, pid: Decimal("0.0018444"))
    monkeypatch.setattr(
        accounting._reclaim,
        "_close_created_empty_accounts",
        lambda *args: {"reclaimed_lamports": 1_844_400, "signature": "rent", "accounts": ["ata"]},
    )
    monkeypatch.setattr(accounting._live, "_notify", lambda *args: None)

    result = accounting._close_live_rent_aware(app, "123", position, Decimal(1), "FULL")
    assert result["closed"] is True
    assert result["net_sol"] == Decimal("0")
    with sol.connect(app) as conn:
        row = conn.execute("SELECT status,realised_net_sol FROM positions WHERE position_id='p1'").fetchone()
    assert row["status"] == "CLOSED"
    assert Decimal(row["realised_net_sol"]) == Decimal("0")
