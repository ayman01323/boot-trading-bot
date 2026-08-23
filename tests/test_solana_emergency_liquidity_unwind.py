from contextlib import closing
from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import solana_emergency_liquidity_unwind_patch as patch


class _Executor:
    telegram_id = "123"
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()


def _cfg():
    return {
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
        "live_emergency_exit_max_combined_bps": "500",
        "live_require_price_impact_quote": "true",
        "live_max_rent_exposure_lamports": "3000000",
    }


def _order(price_impact):
    return {
        "transaction": "AA==",
        "requestId": "req-1",
        "router": "metis",
        "outAmount": "1000000",
        "slippageBps": 30,
        "priceImpact": price_impact,
        "routePlan": [{"swapInfo": {"label": "one-hop"}}],
        "signatureFeeLamports": 5000,
        "prioritizationFeeLamports": 0,
        "rentFeeLamports": 0,
        "feeBps": 0,
    }


def test_normal_exit_keeps_strict_price_impact_guard():
    with pytest.raises(patch._exec.SolanaLiveError, match="quoted price impact"):
        patch.validate_order_with_emergency_liquidity(
            _Executor(),
            _order(4.5),
            "mint",
            patch._sol.WSOL_MINT,
            1_000_000,
            1_000_000,
            100_000,
            _cfg(),
        )


def test_stop_loss_may_use_bounded_five_percent_exit_ceiling():
    token = patch._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        result = patch.validate_order_with_emergency_liquidity(
            _Executor(),
            _order(4.5),
            "mint",
            patch._sol.WSOL_MINT,
            1_000_000,
            1_000_000,
            100_000,
            _cfg(),
        )
    finally:
        patch._EXIT_REASON.reset(token)
    assert result["_price_impact_bps"] == "450.0"


def test_stop_loss_still_refuses_a_100_percent_impact_quote():
    token = patch._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        with pytest.raises(patch._exec.SolanaLiveError, match="10000.00 bps"):
            patch.validate_order_with_emergency_liquidity(
                _Executor(),
                _order(100),
                "mint",
                patch._sol.WSOL_MINT,
                1_000_000,
                1_000_000,
                100_000,
                _cfg(),
            )
    finally:
        patch._EXIT_REASON.reset(token)


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")


def test_emergency_exit_retries_smaller_slice_only_after_prebroadcast_impact_reject(monkeypatch, tmp_path):
    app = _app(tmp_path)
    calls = []

    def fake_close(app_, tid, position, fraction, reason):
        f = Decimal(str(fraction))
        calls.append((f, reason))
        if f in {Decimal("1"), Decimal("0.75")}:
            raise patch._exec.SolanaLiveError(
                "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
            )
        return {"closed": False, "signature": "sig", "reason": reason}

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app_: _cfg())

    result = patch.close_live_with_emergency_liquidity_unwind(
        app,
        "123",
        {"position_id": "p1"},
        Decimal(1),
        "SOLANA_STOP_LOSS",
    )

    assert [row[0] for row in calls] == [Decimal("1"), Decimal("0.75"), Decimal("0.50")]
    assert calls[-1][1] == "SOLANA_STOP_LOSS_LIQUIDITY_PARTIAL_50PCT"
    assert result["liquidity_adaptive_fraction"] == "0.50"
    assert patch._backoff_remaining(app, "p1") > 0


def test_all_unsafe_slices_defer_without_broadcast_retry_spam(monkeypatch, tmp_path):
    app = _app(tmp_path)
    calls = []
    notices = []

    def fake_close(app_, tid, position, fraction, reason):
        calls.append(Decimal(str(fraction)))
        raise patch._exec.SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app_: _cfg())
    monkeypatch.setattr(patch._live, "_notify", lambda app_, tid, text: notices.append(text))

    first = patch.close_live_with_emergency_liquidity_unwind(
        app,
        "123",
        {"position_id": "p2"},
        Decimal(1),
        "SOLANA_LEADER_EXIT_LOSS_CAP",
    )
    first_call_count = len(calls)

    second = patch.close_live_with_emergency_liquidity_unwind(
        app,
        "123",
        {"position_id": "p2"},
        Decimal(1),
        "SOLANA_LEADER_EXIT_LOSS_CAP",
    )

    expected = list(patch._SLICE_FRACTIONS)
    assert calls == expected
    assert first_call_count == len(expected)
    assert first["deferred"] is True
    assert second["deferred"] is True
    assert len(calls) == first_call_count
    assert len(notices) == 1
    assert "No transaction was broadcast" in notices[0]
    assert "100%, 75%, 50% and 25%" in notices[0]


def test_backoff_state_survives_a_fresh_process(monkeypatch, tmp_path):
    app = _app(tmp_path)

    def fake_close(app_, tid, position, fraction, reason):
        raise patch._exec.SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app_: _cfg())
    monkeypatch.setattr(patch._live, "_notify", lambda *a, **k: None)

    patch.close_live_with_emergency_liquidity_unwind(
        app, "123", {"position_id": "p3"}, Decimal(1), "SOLANA_STOP_LOSS",
    )
    state_before = patch._load_backoff(app, "p3")
    assert state_before["attempts"] == 1
    assert state_before["first_blocked_epoch"] > 0

    fresh_state = patch._load_backoff(app, "p3")
    assert fresh_state == state_before


def test_escalation_alert_fires_once_past_the_configured_duration(monkeypatch, tmp_path):
    app = _app(tmp_path)
    notices = []

    def fake_close(app_, tid, position, fraction, reason):
        raise patch._exec.SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app_: _cfg())
    monkeypatch.setattr(patch._live, "_notify", lambda app_, tid, text: notices.append(text))

    import time
    patch._save_backoff(app, "p4", {
        "attempts": 5,
        "next_retry": 0,
        "first_blocked_epoch": int(time.time()) - 30 * 3600,
        "last_escalation_epoch": 0,
    })

    patch.close_live_with_emergency_liquidity_unwind(
        app, "123", {"position_id": "p4", "mint": "TestMint111"}, Decimal(1), "SOLANA_STOP_LOSS",
    )

    assert len(notices) == 2
    assert "stuck" in notices[1]
    assert "/solanaforceexit p4 CONFIRM" in notices[1]

    state = patch._load_backoff(app, "p4")
    state["next_retry"] = 0
    patch._save_backoff(app, "p4", state)
    notices.clear()
    patch.close_live_with_emergency_liquidity_unwind(
        app, "123", {"position_id": "p4", "mint": "TestMint111"}, Decimal(1), "SOLANA_STOP_LOSS",
    )
    assert len(notices) == 1


def test_force_close_requires_matching_owner_and_open_status(tmp_path):
    app = _app(tmp_path)
    with closing(patch._sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions(position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,"
            "entry_cost_sol,entry_ts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("p5", "123", "leader", "MintXYZ", "LIVE", "OPEN", "1000", "1.0", 0, 0),
        )
        conn.commit()

    with pytest.raises(ValueError, match="does not belong"):
        patch.force_close_live_position(app, "999", "p5")


def test_force_close_uses_the_wider_manual_ceiling(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with closing(patch._sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions(position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,"
            "entry_cost_sol,entry_ts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("p6", "123", "leader", "MintXYZ", "LIVE", "OPEN", "1000", "1.0", 0, 0),
        )
        conn.commit()

    seen_reasons = []

    def fake_close(app_, tid, position, fraction, reason):
        seen_reasons.append(patch._EXIT_REASON.get())
        return {"closed": True, "signature": "sig", "net_sol": "-0.8", "liquidity_adaptive_fraction": "1"}

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    result = patch.force_close_live_position(app, "123", "p6")
    assert seen_reasons == [patch._MANUAL_FORCE_REASON]
    assert result["closed"] is True


def test_write_off_closes_without_any_swap_and_records_full_loss(tmp_path):
    app = _app(tmp_path)
    with closing(patch._sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions(position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,"
            "entry_cost_sol,entry_ts,realised_net_sol,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("p7", "123", "leader", "MintDead", "LIVE", "OPEN", "5000", "2.5", 0, "0", 0),
        )
        conn.commit()

    result = patch.write_off_unsellable_position(app, "123", "p7")
    assert result["written_off_cost_sol"] == "2.5"
    assert result["realised_net_sol"] == "-2.5"

    with closing(patch._sol.connect(app)) as conn:
        row = dict(conn.execute("SELECT * FROM positions WHERE position_id='p7'").fetchone())
    assert row["status"] == "CLOSED"
    assert row["exit_reason"] == "MANUAL_WRITE_OFF_UNSELLABLE"
    assert row["realised_net_sol"] == "-2.5"
    assert row["token_amount_raw"] == "0"


def test_write_off_preserves_any_prior_partial_realised_amount(tmp_path):
    app = _app(tmp_path)
    with closing(patch._sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions(position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,"
            "entry_cost_sol,entry_ts,realised_net_sol,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("p8", "123", "leader", "MintDead", "LIVE", "OPEN", "2000", "1.0", 0, "0.3", 0),
        )
        conn.commit()

    result = patch.write_off_unsellable_position(app, "123", "p8")
    assert result["realised_net_sol"] == "-0.7"


def test_write_off_requires_matching_owner_live_mode_and_open_status(tmp_path):
    app = _app(tmp_path)
    with closing(patch._sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions(position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,"
            "entry_cost_sol,entry_ts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("p9", "123", "leader", "MintXYZ", "SHADOW", "OPEN", "1000", "1.0", 0, 0),
        )
        conn.commit()

    with pytest.raises(ValueError, match="does not belong"):
        patch.write_off_unsellable_position(app, "999", "p9")
    with pytest.raises(ValueError, match="Only LIVE positions"):
        patch.write_off_unsellable_position(app, "123", "p9")
    with pytest.raises(ValueError, match="Unknown Solana position"):
        patch.write_off_unsellable_position(app, "123", "does-not-exist")
