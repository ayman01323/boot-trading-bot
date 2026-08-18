from types import SimpleNamespace

import pytest

from learnerbot import solana_exit_circuit_breaker_patch as circuit
from learnerbot import solana_live_executor as executor
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data", telegram_bot_token="")


def _position(app):
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
        return dict(conn.execute("SELECT * FROM positions WHERE position_id='p1'").fetchone())


def test_landed_invalid_exit_opens_circuit_disables_live_and_never_retries(monkeypatch, tmp_path):
    app = _app(tmp_path)
    position = _position(app)
    calls = []

    def landed(*args, **kwargs):
        calls.append(True)
        raise executor.SolanaLivePostExecutionError("no token balance decrease", {"signature": "tx-landed"})

    disabled = []
    monkeypatch.setattr(circuit, "_PREV_CLOSE", landed)
    monkeypatch.setattr(circuit, "set_user_setting", lambda *a, **k: disabled.append((a, k)))
    monkeypatch.setattr(circuit._live, "_notify", lambda *a, **k: None)

    with pytest.raises(executor.SolanaLivePostExecutionError):
        circuit.close_live_guarded(app, "123", position, 1, "STOP_LOSS")

    row = circuit.circuit_row(app, "p1")
    assert row["status"] == "LANDED_INVALID"
    assert row["tx_signature"] == "tx-landed"
    assert len(disabled) == 1

    with sol.connect(app) as conn:
        p = conn.execute("SELECT status,leader_exit_pending,exit_reason FROM positions WHERE position_id='p1'").fetchone()
    assert p["status"] == "OPEN"
    assert p["leader_exit_pending"] == 1
    assert "EXIT_CIRCUIT_LANDED_INVALID" in p["exit_reason"]

    with pytest.raises(executor.SolanaLiveError, match="automatic retry blocked"):
        circuit.close_live_guarded(app, "123", position, 1, "STOP_LOSS")
    assert len(calls) == 1


def test_pre_execution_failure_does_not_open_circuit(monkeypatch, tmp_path):
    app = _app(tmp_path)
    position = _position(app)
    monkeypatch.setattr(
        circuit,
        "_PREV_CLOSE",
        lambda *a, **k: (_ for _ in ()).throw(executor.SolanaLiveError("RPC unavailable before submission")),
    )
    with pytest.raises(executor.SolanaLiveError, match="RPC unavailable"):
        circuit.close_live_guarded(app, "123", position, 1, "STOP_LOSS")
    assert circuit.circuit_row(app, "p1") is None
