from types import SimpleNamespace

from learnerbot import solana_live_position_scope_fix_patch as fix
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "csv")


def _insert_position(app, *, pid, tid, mint, mode):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,
                 token_amount_raw,entry_cost_sol,entry_ts,leader_buy_signature,
                 leader_entry_sol,leader_entry_token_raw,signal_count,current_exit_sol,
                 unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid, str(tid), "leader", 1, mint, mode, "OPEN", "1000", "0.001", 1,
                "sig", "0.001", "1000", 1, "0", "0", 0.0, 0.0, 0, "0", 1,
            ),
        )
        conn.commit()


def test_shadow_position_does_not_match_live_duplicate_guard(tmp_path):
    app = _app(tmp_path)
    _insert_position(app, pid="shadow", tid="1", mint="MINT_A", mode="SHADOW")

    assert fix._open_live_position(app, "1", "MINT_A") is None


def test_live_position_matches_live_duplicate_guard(tmp_path):
    app = _app(tmp_path)
    _insert_position(app, pid="live", tid="1", mint="MINT_B", mode="LIVE")

    row = fix._open_live_position(app, "1", "MINT_B")
    assert row is not None
    assert row["mode"] == "LIVE"


def test_global_status_counts_live_capacity_separately(tmp_path):
    app = _app(tmp_path)
    _insert_position(app, pid="shadow", tid="1", mint="MINT_A", mode="SHADOW")
    _insert_position(app, pid="live", tid="1", mint="MINT_B", mode="LIVE")

    status = fix.status(app)

    assert status["open_positions"] == 1
    assert status["open_live_positions"] == 1
    assert status["open_shadow_positions"] == 1
    assert status["reconcile_required_positions"] == 0
