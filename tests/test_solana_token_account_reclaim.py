from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_sibot as sol
from learnerbot import solana_token_account_reclaim_patch as reclaim


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def test_buy_tracks_only_newly_created_accounts(monkeypatch):
    ex = SimpleNamespace()
    before = {
        "existing": {"pubkey": "existing", "program_id": TOKEN_PROGRAM, "amount": 0, "lamports": 100},
    }
    after = {
        **before,
        "new": {"pubkey": "new", "program_id": TOKEN_PROGRAM, "amount": 123, "lamports": 1_844_400},
    }
    calls = iter([before, after])
    monkeypatch.setattr(reclaim, "_token_accounts", lambda *args: next(calls))
    monkeypatch.setattr(
        reclaim,
        "_PREV_BUY",
        lambda self, mint, amount, reserve: {"signature": "buy", "totalOutputAmount": "123"},
    )

    result = reclaim._buy_track_created_accounts(ex, "mint", Decimal("0.0005"), Decimal("0.005"))
    assert result["token_account_creation_reconciled"] is True
    assert [r["pubkey"] for r in result["bot_created_output_token_accounts"]] == ["new"]


def test_failed_pre_snapshot_never_infers_created_account(monkeypatch):
    ex = SimpleNamespace()
    after = {
        "new": {"pubkey": "new", "program_id": TOKEN_PROGRAM, "amount": 123, "lamports": 1_844_400},
    }
    calls = iter([None, after])
    monkeypatch.setattr(reclaim, "_token_accounts", lambda *args: next(calls))
    monkeypatch.setattr(reclaim, "_PREV_BUY", lambda *args: {"signature": "buy"})

    result = reclaim._buy_track_created_accounts(ex, "mint", Decimal("0.0005"), Decimal("0.005"))
    assert result["token_account_creation_reconciled"] is False
    assert result["bot_created_output_token_accounts"] == []


def test_full_exit_adds_proven_rent_refund_to_realised_pnl(monkeypatch, tmp_path):
    app = _app(tmp_path)
    position = {
        "position_id": "p1",
        "telegram_id": "123",
        "leader_wallet": "leader",
        "mint": "mint",
        "token_amount_raw": "100",
        "entry_cost_sol": "0.0023444",
        "realised_net_sol": "0",
        "leader_exit_pending": 0,
    }
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("p1", "123", "leader", 1, "mint", "LIVE", "OPEN", "100", "0.0023444", 1,
             "buy", "0.0005", "100", 1, "0", "0", 0.0, 0.0, 0, "0", 1),
        )
        conn.commit()

    class Executor:
        address = "ABCDEFGH1234567890"
        def sell(self, mint, raw):
            assert raw == 100
            return {
                "signature": "sell",
                "totalOutputAmount": "500000",
                "wallet_delta_lamports": 500_000,
            }

    monkeypatch.setattr(reclaim._binding, "_resolve_executor", lambda *args: (Executor(), 100))
    monkeypatch.setattr(
        reclaim,
        "_close_created_empty_accounts",
        lambda *args: {"reclaimed_lamports": 1_844_400, "signature": "rent", "accounts": ["ata"]},
    )
    messages = []
    monkeypatch.setattr(reclaim._live, "_notify", lambda app, tid, text: messages.append(text))

    result = reclaim._close_bound_live_with_reclaim(app, "123", position, Decimal(1), "TEST")
    assert result["closed"] is True
    assert result["rent_reclaimed_sol"] == Decimal("0.0018444")
    assert result["net_sol"] == Decimal("0")
    assert "Rent reclaimed" in messages[0]

    with sol.connect(app) as conn:
        row = conn.execute(
            "SELECT status,realised_net_sol FROM positions WHERE position_id='p1'"
        ).fetchone()
    assert row["status"] == "CLOSED"
    assert Decimal(row["realised_net_sol"]) == Decimal("0")


def test_partial_exit_does_not_attempt_account_reclaim(monkeypatch, tmp_path):
    app = _app(tmp_path)
    position = {
        "position_id": "p1",
        "telegram_id": "123",
        "leader_wallet": "leader",
        "mint": "mint",
        "token_amount_raw": "100",
        "entry_cost_sol": "0.001",
        "realised_net_sol": "0",
        "leader_exit_pending": 0,
    }
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("p1", "123", "leader", 1, "mint", "LIVE", "OPEN", "100", "0.001", 1,
             "buy", "0.001", "100", 1, "0", "0", 0.0, 0.0, 0, "0", 1),
        )
        conn.commit()

    class Executor:
        address = "ABCDEFGH1234567890"
        def sell(self, mint, raw):
            return {"signature": "sell", "totalOutputAmount": "600000", "wallet_delta_lamports": 600_000}

    monkeypatch.setattr(reclaim._binding, "_resolve_executor", lambda *args: (Executor(), 100))
    called = []
    monkeypatch.setattr(reclaim, "_close_created_empty_accounts", lambda *args: called.append(True))
    monkeypatch.setattr(reclaim._live, "_notify", lambda *args: None)

    result = reclaim._close_bound_live_with_reclaim(app, "123", position, Decimal("0.5"), "PARTIAL")
    assert result["closed"] is False
    assert called == []


def test_late_confirmed_rent_close_is_reconciled_from_chain_delta(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        reclaim._ensure_schema(conn)
        conn.execute(
            """INSERT INTO live_position_created_token_accounts(
                 position_id,account_pubkey,program_id,entry_lamports,created_at,pending_signature
               ) VALUES(?,?,?,?,?,?)""",
            ("p1", "ata", TOKEN_PROGRAM, "1844400", 1, "renttx"),
        )
        conn.commit()

    executor = SimpleNamespace(app=app, address="wallet")
    monkeypatch.setattr(reclaim, "_token_accounts", lambda *args: {})
    monkeypatch.setattr(reclaim, "_wallet_delta_from_transaction", lambda *args: 1_839_400)

    result = reclaim._close_created_empty_accounts(executor, "p1", "mint")
    assert result["reclaimed_lamports"] == 1_839_400
    assert result["signature"] == "renttx"
    with sol.connect(app) as conn:
        row = conn.execute(
            """SELECT closed_at,close_signature,pending_signature,reclaimed_lamports
               FROM live_position_created_token_accounts WHERE position_id='p1' AND account_pubkey='ata'"""
        ).fetchone()
    assert row["closed_at"] is not None
    assert row["close_signature"] == "renttx"
    assert row["pending_signature"] is None
    assert int(row["reclaimed_lamports"]) == 1_839_400


def test_multi_account_audit_shares_sum_to_real_wallet_refund():
    rows = [
        {"account_pubkey": "a", "entry_lamports": "100"},
        {"account_pubkey": "b", "entry_lamports": "300"},
    ]
    shares = reclaim._shares(rows, 397)
    assert shares["a"] + shares["b"] == 397
    assert shares["b"] > shares["a"]
