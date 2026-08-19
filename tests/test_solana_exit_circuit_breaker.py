from types import SimpleNamespace

import pytest

from learnerbot import solana_execution_validation_patch as validation
from learnerbot import solana_exit_circuit_breaker_patch as circuit
from learnerbot import solana_live_executor as executor
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(
        csv_dir=tmp_path / "CSVbot",
        data_dir=tmp_path / "data",
        telegram_bot_token="",
    )


def _position(app):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "p1",
                "123",
                "leader",
                1,
                "mint",
                "LIVE",
                "OPEN",
                "100",
                "0.001",
                1,
                "buy",
                "0.001",
                "100",
                1,
                "0",
                "0",
                0.0,
                0.0,
                0,
                "0",
                1,
            ),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM positions WHERE position_id='p1'"
            ).fetchone()
        )


def test_landed_invalid_exit_opens_circuit_disables_live_and_never_retries(
    monkeypatch, tmp_path
):
    app = _app(tmp_path)
    position = _position(app)
    calls = []

    def landed(*args, **kwargs):
        calls.append(True)
        raise executor.SolanaLivePostExecutionError(
            "no token balance decrease", {"signature": "tx-landed"}
        )

    disabled = []
    monkeypatch.setattr(circuit, "_PREV_CLOSE", landed)
    monkeypatch.setattr(
        circuit, "set_user_setting", lambda *a, **k: disabled.append((a, k))
    )
    monkeypatch.setattr(circuit._live, "_notify", lambda *a, **k: None)

    with pytest.raises(executor.SolanaLivePostExecutionError):
        circuit.close_live_guarded(
            app, "123", position, 1, "STOP_LOSS"
        )

    row = circuit.circuit_row(app, "p1")
    assert row["status"] == "LANDED_INVALID"
    assert row["tx_signature"] == "tx-landed"
    assert len(disabled) == 1

    with sol.connect(app) as conn:
        p = conn.execute(
            "SELECT status,leader_exit_pending,exit_reason FROM positions WHERE position_id='p1'"
        ).fetchone()
    assert p["status"] == "OPEN"
    assert p["leader_exit_pending"] == 1
    assert "EXIT_CIRCUIT_LANDED_INVALID" in p["exit_reason"]

    with pytest.raises(
        executor.SolanaLiveError, match="automatic.*retry blocked"
    ):
        circuit.close_live_guarded(
            app, "123", position, 1, "STOP_LOSS"
        )
    assert len(calls) == 1


def test_ambiguous_jupiter_success_enters_reconciling_not_landed_invalid(
    monkeypatch, tmp_path
):
    app = _app(tmp_path)
    position = _position(app)
    calls = []

    def ambiguous(*args, **kwargs):
        calls.append(True)
        raise validation.SolanaLiveReconciliationPending(
            "transaction metadata is not yet visible",
            {
                "signature": "tx-pending",
                "requested_sell_raw": 50,
                "totalOutputAmount": "250000",
            },
        )

    disabled = []
    monkeypatch.setattr(circuit, "_PREV_CLOSE", ambiguous)
    monkeypatch.setattr(
        circuit, "set_user_setting", lambda *a, **k: disabled.append((a, k))
    )
    monkeypatch.setattr(circuit._live, "_notify", lambda *a, **k: None)

    with pytest.raises(validation.SolanaLiveReconciliationPending):
        circuit.close_live_guarded(
            app, "123", position, 0.5, "STOP_LOSS"
        )

    row = circuit.circuit_row(app, "p1")
    assert row["status"] == "RECONCILING"
    assert row["tx_signature"] == "tx-pending"
    assert row["sell_raw"] == "50"
    assert row["fraction"] == "0.5"
    assert row["close_reason"] == "STOP_LOSS"
    assert len(disabled) == 1

    with pytest.raises(
        executor.SolanaLiveError, match="automatic.*retry blocked"
    ):
        circuit.close_live_guarded(
            app, "123", position, 0.5, "STOP_LOSS"
        )
    assert len(calls) == 1


def test_reconciler_accounts_existing_signature_without_broadcasting_second_sell(
    monkeypatch, tmp_path
):
    app = _app(tmp_path)
    position = _position(app)
    close_calls = []

    def ambiguous(*args, **kwargs):
        close_calls.append(True)
        raise validation.SolanaLiveReconciliationPending(
            "transaction metadata is not yet visible",
            {
                "signature": "tx-pending",
                "requested_sell_raw": 50,
                "totalInputAmount": "50",
                "totalOutputAmount": "250000",
            },
        )

    monkeypatch.setattr(circuit, "_PREV_CLOSE", ambiguous)
    monkeypatch.setattr(circuit, "set_user_setting", lambda *a, **k: None)
    monkeypatch.setattr(circuit._live, "_notify", lambda *a, **k: None)

    with pytest.raises(validation.SolanaLiveReconciliationPending):
        circuit.close_live_guarded(
            app, "123", position, 0.5, "STOP_LOSS"
        )

    fake_executor = SimpleNamespace(address="wallet")
    monkeypatch.setattr(
        circuit._binding,
        "_resolve_executor",
        lambda *args: (fake_executor, 50),
    )
    monkeypatch.setattr(
        circuit,
        "_chain_sell_evidence",
        lambda *args: {
            "visible": True,
            "tx_ok": True,
            "token_delta_raw": -50,
            "wallet_delta_lamports": 230000,
            "slot": 999,
        },
    )

    finalized = []

    def finalize(app_, tid, position_, fraction, reason, trade, sell_raw):
        finalized.append(
            {
                "tid": tid,
                "fraction": fraction,
                "reason": reason,
                "trade": dict(trade),
                "sell_raw": sell_raw,
            }
        )
        with sol.connect(app_) as conn:
            conn.execute(
                "UPDATE positions SET exit_signature=?,token_amount_raw=?,updated_at=? WHERE position_id=?",
                ("tx-pending", "50", 2, position_["position_id"]),
            )
            conn.commit()
        return {"closed": False, "signature": "tx-pending"}

    monkeypatch.setattr(
        circuit._rent, "finalize_reconciled_live_sell", finalize
    )

    assert circuit.reconcile_pending_exit_circuits(app) == 1
    assert len(close_calls) == 1
    assert len(finalized) == 1
    assert finalized[0]["sell_raw"] == 50
    assert finalized[0]["trade"]["wallet_delta_lamports"] == 230000
    assert finalized[0]["trade"]["recovered_from_exit_circuit"] is True
    assert circuit.circuit_row(app, "p1")["status"] == "RECONCILED"


def test_pre_execution_failure_does_not_open_circuit(monkeypatch, tmp_path):
    app = _app(tmp_path)
    position = _position(app)
    monkeypatch.setattr(
        circuit,
        "_PREV_CLOSE",
        lambda *a, **k: (_ for _ in ()).throw(
            executor.SolanaLiveError("RPC unavailable before submission")
        ),
    )
    with pytest.raises(
        executor.SolanaLiveError, match="RPC unavailable"
    ):
        circuit.close_live_guarded(
            app, "123", position, 1, "STOP_LOSS"
        )
    assert circuit.circuit_row(app, "p1") is None
