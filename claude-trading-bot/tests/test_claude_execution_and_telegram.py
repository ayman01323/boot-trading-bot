"""Composed-path tests for solana_execution_risk_patch.py and
telegram_control_patch.py -- the parts of the owner-required regression list
that need learnerbot actually importable (Linux/WSL only: some patches in
learnerbot's chain import POSIX-only fcntl).

These prove the runtime PATH, not just the pure functions: guarded_buy/sell
as installed onto SolanaLiveExecutor, and handle_update as installed onto
telegram_ui, so later monkey patches/wrapper/import order cannot silently
bypass what test_claude_risk_and_state.py already proves about the
underlying risk/state logic.

DB and network access (position history, live SOL/USD price) are
monkeypatched at solana_execution_risk_patch.position_snapshot -- a seam
this module exposes specifically so the guard's decision logic can be
exercised without a real database or a real Jupiter call. The DB query
functions themselves are unchanged carryover from the previously-reviewed
version of this file (see README.md) and are not re-tested here.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

THIS_DIR = Path(__file__).resolve().parent
BOT_DIR = THIS_DIR.parent
REPO_ROOT = BOT_DIR.parent
for path in (BOT_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import claude_bot_quarantine

claude_bot_quarantine.quarantine_before_any_learnerbot_import()

import claude_state
import risk_engine_guard
import signing_interface
import solana_execution_risk_patch as guard
import telegram_control_patch as router
from learnerbot import telegram_ui as _ui

OWNER_ID = "700000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLAUDE_BOT_WALLET_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("AUTHORISED_CHAINS", "solana")
    monkeypatch.setenv(risk_engine_guard.CAPITAL_BASIS_VAR, "1000")


@pytest.fixture(autouse=True)
def _guard_installed():
    """armed_health_check() (review, 2026-08-26, strengthened same day)
    proves FULL composition -- quarantine, claude_state, telegram router,
    both execution guards, EVM denial -- not just buy/sell identity.
    claude_bot_patches.install_all() is idempotent, so installing everything
    before every test (matching real runtime order) is cheap and correct
    regardless of module import order."""
    import claude_bot_patches

    claude_bot_patches.install_all()


@pytest.fixture
def app(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(
        data_dir=str(data_dir),
        csv_dir=str(tmp_path / "CSVbot"),
        telegram_bot_token="TESTTOKEN",
        operator_settings=lambda: {"engine_enabled": "true"},
    )


@pytest.fixture(autouse=True)
def _ready_signer(monkeypatch):
    monkeypatch.setattr(
        signing_interface, "get_signer_status",
        lambda app: signing_interface.SignerStatus(ready=True, reason="SIGNER_READY=true (test)", address="TESTADDR"),
    )


@pytest.fixture
def snapshot(monkeypatch):
    """Controls what position_snapshot() reports (exposure/open-count only
    -- equity/drawdown is a separate concern, see the `equity` fixture), so
    guard tests don't need a real SQLite DB or a real Jupiter price call.
    Returned dict is mutable by the test."""
    state = {"price_usd": Decimal("100"), "exposure_usd": Decimal("0"), "open_positions": 0}

    def _fake_snapshot(app, telegram_id):
        return dict(state)

    monkeypatch.setattr(guard, "position_snapshot", _fake_snapshot)
    return state


@pytest.fixture
def equity(monkeypatch):
    """Controls what compute_current_equity_usd() reports, so drawdown
    tests don't need a real SQLite DB or a real Jupiter price call. Defaults
    to equity == capital basis (0% drawdown); tests override equity_usd
    directly to simulate unrealised loss, realised loss, or profit."""
    state = {"price_usd": Decimal("100"), "unrealized_pnl_usd": Decimal("0"), "cumulative_realized_pnl_usd": Decimal("0"), "equity_usd": Decimal("1000")}

    def _fake_equity(app, telegram_id, *, capital_basis_usd):
        return dict(state)

    monkeypatch.setattr(guard, "compute_current_equity_usd", _fake_equity)
    return state


@pytest.fixture
def executor(app):
    obj = SimpleNamespace()
    obj.app = app
    obj.telegram_id = OWNER_ID
    return obj


@pytest.fixture(autouse=True)
def _capture_original_calls(monkeypatch):
    calls = {"buy": 0, "sell": 0}

    def _fake_buy(self, output_mint, amount_sol, reserve_sol):
        calls["buy"] += 1
        return {"ok": True}

    def _fake_sell(self, input_mint, amount_raw):
        calls["sell"] += 1
        return {"ok": True}

    monkeypatch.setattr(guard, "_original_buy", _fake_buy)
    monkeypatch.setattr(guard, "_original_sell", _fake_sell)
    return calls


# ---------------------------------------------------------------------------
# solana_execution_risk_patch.py -- guarded_buy / guarded_sell composed path
# ---------------------------------------------------------------------------


def test_buy_refused_when_not_armed(executor, app, snapshot, equity, _capture_original_calls):
    with pytest.raises(guard.ExecutionGuardError, match="Not ARMED"):
        guard._guarded_buy(executor, "MINT", "0.01", "0.02")
    assert _capture_original_calls["buy"] == 0


def test_buy_refused_when_halted_even_if_state_says_armed(executor, app, snapshot, equity, _capture_original_calls):
    claude_state.arm(app, owner_id=OWNER_ID)
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("25.00"), drawdown_usd=Decimal("250"))
    with pytest.raises(guard.ExecutionGuardError, match="HALTED_DRAWDOWN"):
        guard._guarded_buy(executor, "MINT", "0.01", "0.02")
    assert _capture_original_calls["buy"] == 0


def test_buy_succeeds_when_armed_and_within_limits(executor, app, snapshot, equity, _capture_original_calls):
    claude_state.arm(app, owner_id=OWNER_ID)
    result = guard._guarded_buy(executor, "MINT", "0.01", "0.02")
    assert result == {"ok": True}
    assert _capture_original_calls["buy"] == 1


def test_buy_refused_exposure_increasing_over_cap(executor, app, snapshot, equity, _capture_original_calls):
    claude_state.arm(app, owner_id=OWNER_ID)
    snapshot["exposure_usd"] = Decimal("299")  # near the 30% ceiling already
    snapshot["price_usd"] = Decimal("100")
    # 0.011 SOL * $100 = $1.10 proposed (well within the 3% per-position cap)
    # -- total exposure $300.10 is what pushes past the 30% aggregate ceiling.
    with pytest.raises(risk_engine_guard.RiskGuardConfigError, match="aggregate exposure"):
        guard._guarded_buy(executor, "MINT", "0.011", "0.02")
    assert _capture_original_calls["buy"] == 0


def test_buy_refused_on_unrealised_drawdown_breach_and_sends_owner_alert(executor, app, snapshot, equity, _capture_original_calls, monkeypatch):
    """Equity dropping to exactly 20% below HWM from OPEN-position
    mark-to-market alone (no realised trade at all) must latch+refuse --
    the exact gap review blocker 1 flagged (closed-P&L-only missed this)."""
    claude_state.arm(app, owner_id=OWNER_ID)
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=Decimal("20.00"))
    equity["equity_usd"] = Decimal("800.00")  # unrealised loss only -- exactly 20% of HWM 1000

    sent = []
    monkeypatch.setattr(guard, "_send_owner_drawdown_alert", lambda app, **kw: sent.append(kw))

    with pytest.raises(guard.ExecutionGuardError, match="[Dd]rawdown"):
        guard._guarded_buy(executor, "MINT", "0.001", "0.02")

    assert _capture_original_calls["buy"] == 0
    state = claude_state.load_state(app)
    assert state["halted_drawdown"] is True
    assert state["operating_state"] == claude_state.OFF
    assert len(sent) == 1  # owner alert sent exactly once for the FIRST latch

    # a second breached attempt must NOT resend the alert (already latched)
    with pytest.raises(guard.ExecutionGuardError, match="HALTED_DRAWDOWN"):
        guard._guarded_buy(executor, "MINT", "0.001", "0.02")
    assert len(sent) == 1


def test_buy_refused_when_health_check_fails_even_if_armed(executor, app, snapshot, equity, _capture_original_calls, monkeypatch):
    claude_state.arm(app, owner_id=OWNER_ID)
    monkeypatch.setattr(guard, "armed_health_check", lambda app, tid: "kill-switch active (test)")
    # _guarded_buy doesn't call armed_health_check directly today (it composes
    # the same underlying checks) -- this proves the SAME underlying signer
    # check both paths share still blocks consistently.
    monkeypatch.setattr(
        signing_interface, "get_signer_status",
        lambda app: signing_interface.SignerStatus(ready=False, reason="SIGNER_READY=false: forced for test"),
    )
    with pytest.raises(guard.ExecutionGuardError, match="Refusing to sign"):
        guard._guarded_buy(executor, "MINT", "0.01", "0.02")
    assert _capture_original_calls["buy"] == 0


def test_sell_allowed_while_halted_and_while_not_armed(executor, app, snapshot, equity, _capture_original_calls):
    # No arm() call at all -- operating_state is OFF -- and also latch it for good measure.
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("30.00"), drawdown_usd=Decimal("300"))
    result = guard._guarded_sell(executor, "MINT", 1000)
    assert result == {"ok": True}
    assert _capture_original_calls["sell"] == 1


def test_sell_still_requires_valid_signer(executor, app, snapshot, monkeypatch):
    monkeypatch.setattr(
        signing_interface, "get_signer_status",
        lambda app: signing_interface.SignerStatus(ready=False, reason="SIGNER_READY=false: no key"),
    )
    with pytest.raises(guard.ExecutionGuardError, match="Refusing to sign"):
        guard._guarded_sell(executor, "MINT", 1000)


def test_sell_realising_a_loss_latches_immediately_without_a_buy(executor, app, equity, monkeypatch):
    """Review blocker 2: a loss-realising SELL must latch+alert right away,
    not wait for the next BUY attempt. The SELL itself must still succeed."""
    claude_state.arm(app, owner_id=OWNER_ID)
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=Decimal("20.00"))

    before_calls = {"n": 0}

    def _fake_original_sell(self, input_mint, amount_raw):
        before_calls["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(guard, "_original_sell", _fake_original_sell)
    # simulate a loss-realising exit purely through the equity mock -- the
    # post-sell recheck reads compute_current_equity_usd(), not the DB
    # delta, for the LATCH decision (the DB delta is only used to update
    # cumulative_realized_pnl_usd, tested separately below).
    equity["equity_usd"] = Decimal("800.00")  # exactly 20% below the 1000 HWM

    sent = []
    monkeypatch.setattr(guard, "_send_owner_drawdown_alert", lambda app, **kw: sent.append(kw))

    result = guard._guarded_sell(executor, "MINT", 1000)

    assert result == {"ok": True}  # the exit itself was never blocked
    assert before_calls["n"] == 1
    state = claude_state.load_state(app)
    assert state["halted_drawdown"] is True
    assert len(sent) == 1


def _insert_closed_live_position(app, *, position_id: str, telegram_id: str, realised_net_sol: Decimal, closed_at: int = 1700000000) -> None:
    """Minimal valid row in the REAL positions table (real schema, real
    connect()) -- these reconciliation tests exercise the actual SQL, not a
    mocked stand-in, since the query itself (identity by position_id) is
    exactly what review asked to be proven crash-safe."""
    from contextlib import closing as _closing

    from learnerbot import solana_sibot as _sol

    with _closing(_sol.connect(app)) as conn:
        conn.execute(
            "INSERT INTO positions (position_id, telegram_id, leader_wallet, mint, mode, status, "
            "token_amount_raw, entry_cost_sol, entry_ts, realised_net_sol, closed_at, updated_at) "
            "VALUES (?, ?, 'LEADER', 'MINT', 'LIVE', 'CLOSED', '0', '1', 1699999000, ?, ?, ?)",
            (position_id, str(telegram_id), str(realised_net_sol), closed_at, closed_at),
        )
        conn.commit()


def test_reconcile_accounts_a_real_closed_position_from_the_db(app, monkeypatch):
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("100"))
    _insert_closed_live_position(app, position_id="pos-A", telegram_id=OWNER_ID, realised_net_sol=Decimal("-2"))

    newly = guard.reconcile_realized_pnl(app, OWNER_ID)

    assert len(newly) == 1
    assert newly[0]["position_id"] == "pos-A"
    state = claude_state.load_state(app)
    assert Decimal(state["cumulative_realized_pnl_usd"]) == Decimal("-200")  # -2 SOL * $100
    assert "pos-A" in state["accounted_position_ids"]


def test_reconcile_is_idempotent_second_call_does_not_double_count(app, monkeypatch):
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("100"))
    _insert_closed_live_position(app, position_id="pos-A", telegram_id=OWNER_ID, realised_net_sol=Decimal("-2"))

    guard.reconcile_realized_pnl(app, OWNER_ID)
    second = guard.reconcile_realized_pnl(app, OWNER_ID)  # nothing new closed since

    assert second == []
    assert Decimal(claude_state.load_state(app)["cumulative_realized_pnl_usd"]) == Decimal("-200")


def test_reconcile_accounts_two_closed_positions_independently(app, monkeypatch):
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("100"))
    _insert_closed_live_position(app, position_id="pos-A", telegram_id=OWNER_ID, realised_net_sol=Decimal("1"))
    _insert_closed_live_position(app, position_id="pos-B", telegram_id=OWNER_ID, realised_net_sol=Decimal("-3"))

    newly = guard.reconcile_realized_pnl(app, OWNER_ID)

    assert {n["position_id"] for n in newly} == {"pos-A", "pos-B"}
    state = claude_state.load_state(app)
    assert Decimal(state["cumulative_realized_pnl_usd"]) == Decimal("-200")  # (1 - 3) * 100
    assert set(state["accounted_position_ids"]) == {"pos-A", "pos-B"}


def test_reconcile_historical_usd_value_unaffected_by_later_price_change(app, monkeypatch):
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("100"))
    _insert_closed_live_position(app, position_id="pos-A", telegram_id=OWNER_ID, realised_net_sol=Decimal("2"))
    guard.reconcile_realized_pnl(app, OWNER_ID)
    recorded = Decimal(claude_state.load_state(app)["accounted_position_ids"]["pos-A"]["pnl_usd"])

    # SOL price moves a lot, then reconciliation runs again (e.g. next
    # monitor tick) -- pos-A is already accounted, so it must be skipped
    # entirely, not re-priced at the new rate.
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("9999"))
    guard.reconcile_realized_pnl(app, OWNER_ID)
    still_recorded = Decimal(claude_state.load_state(app)["accounted_position_ids"]["pos-A"]["pnl_usd"])

    assert still_recorded == recorded == Decimal("200")


def test_crash_after_db_close_before_claude_accounting_is_recovered_on_next_reconcile(app, monkeypatch):
    """Simulates the exact crash window review flagged: the SELL committed
    to the real positions DB (a real CLOSED row exists), but the process
    died before any Claude-side accounting ran at all -- no call to
    reconcile_realized_pnl happened yet for this position. The very next
    reconciliation pass (what claude_monitor's tick, or the next sell, or
    process startup would trigger) must recover it completely, exactly
    once, not lose it."""
    _insert_closed_live_position(app, position_id="pos-crash", telegram_id=OWNER_ID, realised_net_sol=Decimal("-5"))
    # nothing has run reconcile_realized_pnl yet -- this IS the crash state
    assert claude_state.load_state(app)["accounted_position_ids"] == {}

    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("50"))
    recovered = guard.reconcile_realized_pnl(app, OWNER_ID)  # e.g. the next monitor tick

    assert len(recovered) == 1
    state = claude_state.load_state(app)
    assert Decimal(state["cumulative_realized_pnl_usd"]) == Decimal("-250")  # -5 * 50
    # a second recovery pass (e.g. process restart right after) must not double-count
    second = guard.reconcile_realized_pnl(app, OWNER_ID)
    assert second == []
    assert Decimal(claude_state.load_state(app)["cumulative_realized_pnl_usd"]) == Decimal("-250")


def test_guarded_sell_reconciles_and_latches_via_real_db(executor, app, equity, monkeypatch):
    """End-to-end through the actual guarded_sell call path (not just
    reconcile_realized_pnl directly): a real closed position appearing in
    the DB after _original_sell returns is accounted before the drawdown
    check runs."""
    def _fake_original_sell(self, input_mint, amount_raw):
        _insert_closed_live_position(app, position_id="pos-live", telegram_id=OWNER_ID, realised_net_sol=Decimal("-1"))
        return {"ok": True}

    monkeypatch.setattr(guard, "_original_sell", _fake_original_sell)
    monkeypatch.setattr(guard, "sol_usd_price", lambda: Decimal("100"))

    guard._guarded_sell(executor, "MINT", 1000)

    state = claude_state.load_state(app)
    assert Decimal(state["cumulative_realized_pnl_usd"]) == Decimal("-100")
    assert "pos-live" in state["accounted_position_ids"]


def test_startup_reconciliation_runs_via_app_wrapper(monkeypatch, tmp_path):
    """claude_state.install()'s _app wrapper must call reconcile_realized_pnl
    once at startup (review requirement: "on startup ... reconcile any
    closed positions not yet reflected"), independent of the periodic
    monitor's own tick."""
    import claude_state as cs
    from learnerbot import cli as _cli

    data_dir = tmp_path / "startup_data"
    data_dir.mkdir()
    fake_app = SimpleNamespace(
        data_dir=str(data_dir), csv_dir=str(tmp_path / "startup_csv"),
        telegram_bot_token="", operator_settings=lambda: {"engine_enabled": "true"},
    )

    calls = []
    monkeypatch.setattr(guard, "reconcile_realized_pnl", lambda app, tid: calls.append((app, tid)) or [])
    monkeypatch.setattr(_cli, "_app", lambda: fake_app)  # the "real" loader this test simulates
    monkeypatch.setattr(cs, "_INSTALLED", False)

    cs.install()  # must capture OUR fake loader as _PREV_APP, then install the real wrapper on top
    _cli._app()  # simulates the real first AppSettings.load() at process start

    assert len(calls) == 1
    assert calls[0][0] is fake_app


def test_buy_refused_when_chain_not_authorised(executor, app, snapshot, monkeypatch):
    claude_state.arm(app, owner_id=OWNER_ID)
    monkeypatch.delenv("AUTHORISED_CHAINS", raising=False)
    with pytest.raises(guard.ExecutionGuardError, match="AUTHORISED_CHAINS"):
        guard._guarded_buy(executor, "MINT", "0.01", "0.02")


# ---------------------------------------------------------------------------
# telegram_control_patch.py -- the one authoritative command router
# ---------------------------------------------------------------------------


def _msg(text, sender_id, chat_id="12345"):
    return {"message": {"text": text, "chat": {"id": chat_id}, "from": {"id": sender_id}}}


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(router, "_send", lambda app, chat_id, text: out.append((chat_id, text)))
    return out


def test_router_commands_set_is_exactly_the_six_owner_specified_names():
    assert router.COMMANDS == {
        "/claude_status",
        "/claude_arm_live",
        "/claude_disarm",
        "/claude_stop",
        "/claude_restart_request",
        "/claude_restart_confirm",
    }


def test_non_owner_cannot_status_arm_disarm_stop_or_restart(app, sent):
    for text in (
        "/claude_status",
        "/claude_arm_live CONFIRM",
        "/claude_disarm",
        "/claude_stop",
        "/claude_restart_request",
        "/claude_restart_confirm CONFIRM",
    ):
        sent.clear()
        router.handle_update(app, _msg(text, sender_id="999999999999"))
        assert len(sent) == 1
        assert "Not authorised" in sent[0][1]
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_owner_status_is_read_only_and_always_available(app, sent, snapshot, equity):
    router.handle_update(app, _msg("/claude_status", sender_id=OWNER_ID))
    assert len(sent) == 1
    assert "STATUS" in sent[0][1]
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_arm_requires_literal_confirm_word(app, sent):
    router.handle_update(app, _msg("/claude_arm_live", sender_id=OWNER_ID))
    assert "exactly" in sent[-1][1]
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF

    router.handle_update(app, _msg("/claude_arm_live yes", sender_id=OWNER_ID))
    assert "exactly" in sent[-1][1]
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_owner_arm_then_disarm_then_stop(app, sent):
    router.handle_update(app, _msg("/claude_arm_live CONFIRM", sender_id=OWNER_ID))
    assert "ARMED" in sent[-1][1]
    assert claude_state.load_state(app)["operating_state"] == claude_state.ARMED

    router.handle_update(app, _msg("/claude_disarm", sender_id=OWNER_ID))
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF

    router.handle_update(app, _msg("/claude_arm_live CONFIRM", sender_id=OWNER_ID))
    router.handle_update(app, _msg("/claude_stop", sender_id=OWNER_ID))
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_arm_refused_while_halted_via_router(app, sent):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    router.handle_update(app, _msg("/claude_arm_live CONFIRM", sender_id=OWNER_ID))
    assert "refused" in sent[-1][1].lower()
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_non_owner_cannot_clear_drawdown_latch_via_router(app, sent):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    router.handle_update(app, _msg("/claude_restart_request", sender_id="999999999999"))
    router.handle_update(app, _msg("/claude_restart_confirm CONFIRM", sender_id="999999999999"))
    assert claude_state.load_state(app)["halted_drawdown"] is True


def test_owner_two_step_restart_via_router_clears_latch(app, sent, equity):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=Decimal("20.00"))
    router.handle_update(app, _msg("/claude_restart_request", sender_id=OWNER_ID))
    assert "challenge issued" in sent[-1][1].lower()

    equity["equity_usd"] = Decimal("850.00")  # still below the old HWM -- reset must adopt this as the NEW HWM
    router.handle_update(app, _msg("/claude_restart_confirm CONFIRM", sender_id=OWNER_ID))
    assert "cleared" in sent[-1][1].lower()
    state = claude_state.load_state(app)
    assert state["halted_drawdown"] is False
    # still OFF -- clearing the latch never re-arms
    assert state["operating_state"] == claude_state.OFF
    # fresh baseline established: HWM reset to current equity, not left at
    # the old (higher, pre-drawdown) value
    assert Decimal(state["high_water_equity_usd"]) == Decimal("850.00")


def test_replayed_restart_confirm_rejected_via_router(app, sent):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    router.handle_update(app, _msg("/claude_restart_request", sender_id=OWNER_ID))
    router.handle_update(app, _msg("/claude_restart_confirm CONFIRM", sender_id=OWNER_ID))
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("21.00"), drawdown_usd=Decimal("210"))
    router.handle_update(app, _msg("/claude_restart_confirm CONFIRM", sender_id=OWNER_ID))
    assert "not authorised" in sent[-1][1].lower()
    assert claude_state.load_state(app)["halted_drawdown"] is True


def test_unrelated_command_falls_through_to_previous_handler(app, monkeypatch):
    called = {}
    monkeypatch.setattr(router, "_PREV_HANDLE_UPDATE", lambda app, u: called.setdefault("hit", True))
    router.handle_update(app, _msg("/menu", sender_id=OWNER_ID))
    assert called.get("hit") is True


# ---------------------------------------------------------------------------
# claude_monitor.py -- periodic, non-trading drawdown/health monitor
# (review, 2026-08-26, blockers 2 and 3)
# ---------------------------------------------------------------------------


def test_armed_health_check_passes_when_everything_is_fine(app):
    assert guard.armed_health_check(app, OWNER_ID) is None


def test_armed_health_check_fails_when_signer_not_ready(app, monkeypatch):
    monkeypatch.setattr(
        signing_interface, "get_signer_status",
        lambda app: signing_interface.SignerStatus(ready=False, reason="SIGNER_READY=false: test"),
    )
    assert guard.armed_health_check(app, OWNER_ID) is not None


def test_armed_health_check_fails_when_kill_switch_active(app):
    # operator_settings(), not general() -- the real bug review caught
    # (2026-08-26): the check used to read a CSV that doesn't carry
    # engine_enabled at all, so it was silently always-on.
    app.operator_settings = lambda: {"engine_enabled": "false"}
    reason = guard.armed_health_check(app, OWNER_ID)
    assert reason is not None
    assert "kill-switch" in reason


def test_armed_health_check_ignores_general_and_only_reads_operator_settings(app):
    # A stale/irrelevant engine_enabled=false sitting in general() (the
    # wrong file) must have zero effect -- proves the fix isn't reading both.
    app.general = lambda: {"engine_enabled": "false"}
    app.operator_settings = lambda: {"engine_enabled": "true"}
    assert guard.armed_health_check(app, OWNER_ID) is None


@pytest.mark.parametrize(
    "break_it,expected_fragment",
    [
        ("quarantine", "quarantine"),
        ("state_machine", "state machine"),
        ("router", "Telegram router"),
        ("buy_guard", "BUY guard"),
        ("sell_guard", "SELL guard"),
        ("evm_guard", "EVM execution"),
    ],
)
def test_armed_health_check_fails_when_any_composition_component_breaks(app, monkeypatch, break_it, expected_fragment):
    """Review, 2026-08-26: buy/sell identity alone wasn't proof the whole
    Claude runtime composition was intact. Each of these must independently
    fail the check."""
    import claude_bot_quarantine
    import evm_execution_guard_patch as evm_guard
    from learnerbot import config as _learnerbot_config
    from learnerbot import live_executor as _evm_executor
    from learnerbot import solana_live_executor as _executor

    if break_it == "quarantine":
        monkeypatch.setattr(_learnerbot_config, "load_dotenv", lambda *a, **k: None)
    elif break_it == "state_machine":
        monkeypatch.setattr(claude_state, "_INSTALLED", False)
    elif break_it == "router":
        monkeypatch.setattr(_ui, "handle_update", lambda app, u: None)
    elif break_it == "buy_guard":
        monkeypatch.setattr(_executor.SolanaLiveExecutor, "buy", guard._original_buy)
    elif break_it == "sell_guard":
        monkeypatch.setattr(_executor.SolanaLiveExecutor, "sell", guard._original_sell)
    elif break_it == "evm_guard":
        monkeypatch.setattr(_evm_executor.LiveTrader, "buy", evm_guard._original_buy)

    reason = guard.armed_health_check(app, OWNER_ID)
    assert reason is not None
    assert expected_fragment.lower() in reason.lower()


def test_monitor_actively_disarms_when_composition_breaks_while_armed(app, snapshot, equity, monkeypatch):
    """Same as test_monitor_actively_disarms_when_armed_and_health_check_fails
    but exercising a REAL composition break (router displaced) through the
    real armed_health_check(), not a monkeypatched stand-in for it."""
    import claude_monitor

    claude_state.arm(app, owner_id=OWNER_ID)
    monkeypatch.setattr(_ui, "handle_update", lambda app, u: None)  # displace the router

    sent = []
    monkeypatch.setattr(guard, "_send_owner_health_alert", lambda app, **kw: sent.append(kw))

    claude_monitor.check_once(app)

    state = claude_state.load_state(app)
    assert state["operating_state"] == claude_state.OFF
    assert "router" in state["last_forced_off_reason"].lower()
    assert len(sent) == 1


def test_monitor_actively_disarms_when_armed_and_health_check_fails(app, snapshot, equity, monkeypatch):
    import claude_monitor

    claude_state.arm(app, owner_id=OWNER_ID)
    assert claude_state.load_state(app)["operating_state"] == claude_state.ARMED

    monkeypatch.setattr(guard, "armed_health_check", lambda app, tid: "signer/identity: forced for test")
    sent = []
    monkeypatch.setattr(guard, "_send_owner_health_alert", lambda app, **kw: sent.append(kw))

    claude_monitor.check_once(app)

    state = claude_state.load_state(app)
    assert state["operating_state"] == claude_state.OFF
    assert "forced for test" in state["last_forced_off_reason"]
    assert len(sent) == 1


def test_monitor_does_not_touch_state_when_not_armed(app, snapshot, equity, monkeypatch):
    import claude_monitor

    monkeypatch.setattr(guard, "armed_health_check", lambda app, tid: "would fail if checked")
    forced = []
    monkeypatch.setattr(claude_state, "force_off", lambda app, **kw: forced.append(kw))

    claude_monitor.check_once(app)  # operating_state is OFF -- health check must not even run

    assert forced == []


def test_monitor_latches_on_unrealised_drawdown_with_no_buy_or_sell(app, snapshot, equity, monkeypatch):
    import claude_monitor

    claude_state.arm(app, owner_id=OWNER_ID)
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=Decimal("20.00"))
    equity["equity_usd"] = Decimal("800.00")  # pure mark-to-market move, no trade at all

    sent = []
    monkeypatch.setattr(guard, "_send_owner_drawdown_alert", lambda app, **kw: sent.append(kw))

    claude_monitor.check_once(app)

    assert claude_state.load_state(app)["halted_drawdown"] is True
    assert len(sent) == 1


def test_monitor_has_no_code_path_to_arm_clear_or_broadcast():
    """Structural, not behavioral: read claude_monitor.py's own source and
    confirm it contains no reference to any arming/latch-clearing/signing
    function name at all -- not just that a particular test scenario didn't
    trigger one."""
    text = (BOT_DIR / "claude_monitor.py").read_text(encoding="utf-8")
    forbidden = (
        "claude_state.arm(", "claude_state.confirm_restart(", "claude_state.issue_restart_challenge(",
        "claude_state.reset_high_water_to_current(", "_original_buy", "_original_sell",
        "get_signing_keypair_bytes", "SolanaWalletStore",
    )
    for token in forbidden:
        assert token not in text, f"claude_monitor.py must never reference {token!r}"


# ---------------------------------------------------------------------------
# claude_bot_patches.install_all() -- structural wiring proof
# ---------------------------------------------------------------------------


def test_install_all_wires_exactly_the_expected_hooks():
    import claude_bot_patches
    from learnerbot import cli as _cli
    from learnerbot import solana_live_executor as _executor

    claude_bot_patches.install_all()

    assert _executor.SolanaLiveExecutor.buy is guard._guarded_buy
    assert _executor.SolanaLiveExecutor.sell is guard._guarded_sell
    assert _ui.handle_update is router.handle_update
    # claude_state.install() must have wrapped _app -- calling it must reset
    # operating state, proven indirectly: the wrapper is not the raw loader.
    assert _cli._app is not claude_state._PREV_APP
