from types import SimpleNamespace
from decimal import Decimal

from learnerbot import solana_sibot as sol


def test_selected_leader_buy_writes_one_shadow_trade(tmp_path, monkeypatch):
    """Prove the non-signing copy path can create one SHADOW position end to end."""
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
    monkeypatch.setattr(
        sol,
        "settings",
        lambda *_args, **_kwargs: {
            "shadow_allocation_sol": "0.05",
            "estimated_entry_fee_sol": "0.00002",
        },
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
    assert len(actions) == 1
    assert actions[0]["action"] == "BUY"
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
