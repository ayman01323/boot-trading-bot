from types import SimpleNamespace
from decimal import Decimal

from learnerbot import solana_sibot as sol
from learnerbot import solana_positive_edge_entry_gate_patch as edge_gate


def test_selected_leader_buy_writes_one_shadow_trade(tmp_path, monkeypatch):
    """Prove one non-signing SHADOW BUY after every composed BUY gate passes."""
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "csv")
    app.data_dir.mkdir()
    app.csv_dir.mkdir()

    monkeypatch.setattr(
        sol,
        "all_users",
        lambda *_args, **_kwargs: [
            {"telegram_id": "master-test", "status": "ACTIVE", "role": "MASTER"}
        ],
    )
    monkeypatch.setattr(sol, "_leader_rank", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        sol._sibot,
        "user_settings",
        lambda *_args, **_kwargs: {"enabled": "true", "min_exit_profit_pct": "0.10"},
    )
    cfg = {
        "shadow_allocation_sol": "0.05",
        "estimated_entry_fee_sol": "0.00002",
        "live_min_leader_median_return_pct": "5",
        "live_min_leader_recent_median_return_pct": "4",
    }
    monkeypatch.setattr(sol, "settings", lambda *_args, **_kwargs: cfg)

    # This is a proof of the composed happy path, not a bypass in runtime code:
    # supply deterministic evidence that would satisfy the final BUY-only gates.
    monkeypatch.setattr(
        edge_gate,
        "_edge_ok",
        lambda *_args, **_kwargs: (
            True,
            "leader edge passes",
            {"median_return_pct": Decimal("8"), "recent_median_return_pct": Decimal("7")},
        ),
    )
    monkeypatch.setattr(edge_gate, "_mint_loss_gate", lambda *_args, **_kwargs: (True, "mint clean", {}))
    monkeypatch.setattr(
        edge_gate,
        "_platform_amount_gate",
        lambda *_args, **_kwargs: (
            True,
            "realised profit amount exceeds loss target",
            {"gross_profit_sol": Decimal("0.04"), "gross_loss_sol": Decimal("0.01"), "profit_factor": Decimal("4")},
            False,
        ),
    )

    monkeypatch.setattr(sol, "_open_position", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sol,
        "_validate_shadow_entry",
        lambda *_args, **_kwargs: (
            True,
            "PASS",
            {"out_raw": 123456789, "deterioration_pct": Decimal("0"), "roundtrip_loss_pct": Decimal("0")},
        ),
    )

    event = {
        "action": "BUY",
        "leader_wallet": "leader-wallet-test",
        "mint": "mint-test",
        "signature": "leader-signature-test",
        "sol_amount": Decimal("1"),
        "token_amount_raw": 2_000_000_000,
    }

    actions = sol.process_leader_event(app, event)
    assert len(actions) == 1, actions
    assert actions[0]["action"] == "BUY", actions
    assert actions[0]["mode"] == "SHADOW"

    with sol.connect(app) as conn:
        row = conn.execute(
            "SELECT telegram_id,leader_wallet,mint,mode,status,token_amount_raw FROM positions"
        ).fetchone()
    assert row is not None
    assert row["telegram_id"] == "master-test"
    assert row["leader_wallet"] == "leader-wallet-test"
    assert row["mint"] == "mint-test"
    assert row["mode"] == "SHADOW"
    assert row["status"] == "OPEN"
    assert int(row["token_amount_raw"]) == 123456789
