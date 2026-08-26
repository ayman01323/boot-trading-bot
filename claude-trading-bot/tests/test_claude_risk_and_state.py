"""Pure-Python tests for risk_engine_guard.py and claude_state.py.

Deliberately do not import anything from `learnerbot` -- these two modules
have no learnerbot dependency at import time, so this file can run on any
platform (including Windows) without the full bootstrap chain. Composed-path
tests that DO need learnerbot (guarded_buy/sell, the Telegram command
router, claude_bot_patches wiring) live in
test_claude_execution_and_telegram.py, which requires Linux/WSL.

Covers the owner-required regression list (2026-08-26 combined instruction,
item 7) for everything expressible without a live executor/DB/Telegram poller.
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

THIS_DIR = Path(__file__).resolve().parent
BOT_DIR = THIS_DIR.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import claude_state
import risk_engine_guard
from risk_engine_guard import quantize_pct


# ---------------------------------------------------------------------------
# risk_engine_guard.py
# ---------------------------------------------------------------------------


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setenv(risk_engine_guard.CAPITAL_BASIS_VAR, "1000")
    return risk_engine_guard.RiskLimits.load()


def test_capital_basis_required(monkeypatch):
    monkeypatch.delenv(risk_engine_guard.CAPITAL_BASIS_VAR, raising=False)
    with pytest.raises(risk_engine_guard.RiskGuardConfigError):
        risk_engine_guard.RiskLimits.load()


def test_capital_basis_must_be_positive_number(monkeypatch):
    monkeypatch.setenv(risk_engine_guard.CAPITAL_BASIS_VAR, "not-a-number")
    with pytest.raises(risk_engine_guard.RiskGuardConfigError):
        risk_engine_guard.RiskLimits.load()
    monkeypatch.setenv(risk_engine_guard.CAPITAL_BASIS_VAR, "0")
    with pytest.raises(risk_engine_guard.RiskGuardConfigError):
        risk_engine_guard.RiskLimits.load()
    monkeypatch.setenv(risk_engine_guard.CAPITAL_BASIS_VAR, "-500")
    with pytest.raises(risk_engine_guard.RiskGuardConfigError):
        risk_engine_guard.RiskLimits.load()


def test_owner_approved_constants_not_env_configurable(monkeypatch, limits):
    # Setting an old-style env var must have zero effect -- these are fixed
    # by direct owner instruction, not configuration.
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "999")
    monkeypatch.setenv("MAX_DRAWDOWN_PCT", "1")
    fresh = risk_engine_guard.RiskLimits.load()
    assert fresh.max_open_positions == 10
    assert fresh.max_drawdown_pct == Decimal("20.00")
    assert fresh.max_position_pct == Decimal("3.00")
    assert fresh.max_total_exposure_pct == Decimal("30.00")


def test_positions_1_through_10_accepted(limits):
    for open_positions in range(10):  # 0..9 open before this proposal -> positions 1..10
        limits.check_new_position(
            proposed_usd=Decimal("1"), current_exposure_usd=Decimal("0"), open_positions=open_positions
        )


def test_11th_position_rejected(limits):
    with pytest.raises(risk_engine_guard.RiskGuardConfigError, match="maximum of 10"):
        limits.check_new_position(proposed_usd=Decimal("1"), current_exposure_usd=Decimal("0"), open_positions=10)


def test_exactly_3pct_position_accepted(limits):
    limits.check_new_position(proposed_usd=Decimal("30.00"), current_exposure_usd=Decimal("0"), open_positions=0)


def test_above_3pct_position_rejected(limits):
    with pytest.raises(risk_engine_guard.RiskGuardConfigError, match="per-position"):
        limits.check_new_position(proposed_usd=Decimal("30.10"), current_exposure_usd=Decimal("0"), open_positions=0)


def test_aggregate_exposure_up_to_30pct_accepted(limits):
    limits.check_new_position(proposed_usd=Decimal("1.00"), current_exposure_usd=Decimal("299"), open_positions=0)


def test_aggregate_exposure_above_30pct_rejected(limits):
    with pytest.raises(risk_engine_guard.RiskGuardConfigError, match="aggregate exposure"):
        limits.check_new_position(proposed_usd=Decimal("1.10"), current_exposure_usd=Decimal("299"), open_positions=0)


def test_drawdown_breached_exception_shape():
    # Drawdown itself is no longer computed by risk_engine_guard (see
    # claude_state.evaluate_drawdown, tested below) -- this only checks the
    # exception type it's still raised as remains constructible/shaped right.
    exc = risk_engine_guard.DrawdownLimitBreached(drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200.00"))
    assert exc.drawdown_pct == Decimal("20.00")
    assert "20.00%" in str(exc)


# ---------------------------------------------------------------------------
# claude_state.py
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(data_dir=str(data_dir), csv_dir=str(tmp_path / "CSVbot"), telegram_bot_token="TESTTOKEN")


def test_default_state_off_not_halted(app):
    state = claude_state.load_state(app)
    assert state["operating_state"] == claude_state.OFF
    assert state["halted_drawdown"] is False
    assert claude_state.effective_state(state) == claude_state.OFF


def test_restart_to_off_when_no_drawdown_latch(app):
    claude_state.arm(app, owner_id="owner1")
    assert claude_state.load_state(app)["operating_state"] == claude_state.ARMED
    state = claude_state.reset_on_startup(app)
    assert state["operating_state"] == claude_state.OFF
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_restart_to_halted_drawdown_when_latch_set(app):
    claude_state.arm(app, owner_id="owner1")
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("25.00"), drawdown_usd=Decimal("250"))
    state = claude_state.reset_on_startup(app)
    assert claude_state.effective_state(state) == "HALTED_DRAWDOWN"
    assert state["halted_drawdown"] is True


def test_armed_never_auto_restored_across_restart(app):
    claude_state.arm(app, owner_id="owner1")
    claude_state.reset_on_startup(app)
    claude_state.reset_on_startup(app)  # idempotent across multiple simulated restarts
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_arm_blocked_while_halted(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    with pytest.raises(claude_state.ClaudeStateError, match="HALTED_DRAWDOWN"):
        claude_state.arm(app, owner_id="owner1")


def test_latch_persists_after_object_reload(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    reloaded = claude_state.load_state(app)  # simulates a fresh object/process reading the same file
    assert reloaded["halted_drawdown"] is True


def test_latch_persists_after_simulated_process_restart(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.reset_on_startup(app)
    assert claude_state.load_state(app)["halted_drawdown"] is True


def test_ordinary_recovery_does_not_clear_latch(app):
    """Nothing in claude_state re-evaluates and auto-clears the latch based on
    a recomputed (possibly lower) drawdown -- only the explicit two-step
    owner flow can clear it. This test proves no such auto-clear path exists:
    latching once and then just re-reading state, with no restart-flow call
    in between, must never show halted_drawdown=False."""
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    for _ in range(5):
        assert claude_state.load_state(app)["halted_drawdown"] is True


def test_corrupt_state_file_fails_closed(app):
    Path(app.data_dir, claude_state.STATE_FILENAME).write_text("{not json", encoding="utf-8")
    state = claude_state.load_state(app)
    assert state["halted_drawdown"] is True
    assert state.get("state_error")


def test_restart_confirm_requires_a_prior_request(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    with pytest.raises(claude_state.ClaudeStateError, match="No valid pending"):
        claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)


def test_restart_confirm_wrong_owner_rejected(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.issue_restart_challenge(app, owner_id="owner1")
    with pytest.raises(claude_state.ClaudeStateError, match="No valid pending"):
        claude_state.confirm_restart(app, owner_id="attacker", precondition_check=lambda: None)
    # still halted -- a wrong-owner confirm must not clear anything
    assert claude_state.load_state(app)["halted_drawdown"] is True


def test_restart_request_only_valid_while_halted(app):
    with pytest.raises(claude_state.ClaudeStateError, match="No drawdown halt"):
        claude_state.issue_restart_challenge(app, owner_id="owner1")


def test_owner_two_step_restart_clears_latch_with_fresh_baseline(app):
    claude_state.arm(app, owner_id="owner1")
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.issue_restart_challenge(app, owner_id="owner1")
    before = time.time()
    state = claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)
    assert state["halted_drawdown"] is False
    assert state["baseline_epoch"] >= int(before)
    assert state["authorized_restart_by"] == "owner1"
    # clearing the latch must NOT re-arm -- a separate /claude_arm_live is required
    assert state["operating_state"] == claude_state.OFF


def test_stale_replayed_confirmation_rejected(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.issue_restart_challenge(app, owner_id="owner1")
    claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)
    # latch it again and try to replay the SAME (already-consumed) challenge flow
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("21.00"), drawdown_usd=Decimal("210"))
    with pytest.raises(claude_state.ClaudeStateError, match="No valid pending"):
        claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)


def test_expired_confirmation_rejected(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.issue_restart_challenge(app, owner_id="owner1")
    # simulate TTL expiry by rewriting the persisted issued_at directly
    path = Path(app.data_dir, claude_state.STATE_FILENAME)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["restart_challenge"]["issued_at"] -= claude_state.RESTART_CHALLENGE_TTL_SECONDS + 5
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(claude_state.ClaudeStateError, match="No valid pending"):
        claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)


def test_restart_confirm_precondition_failure_keeps_halted(app):
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    claude_state.issue_restart_challenge(app, owner_id="owner1")

    def _fail():
        raise RuntimeError("signer not ready")

    with pytest.raises(RuntimeError, match="signer not ready"):
        claude_state.confirm_restart(app, owner_id="owner1", precondition_check=_fail)
    assert claude_state.load_state(app)["halted_drawdown"] is True
    # challenge is single-use even on precondition failure -- must not be replayable
    with pytest.raises(claude_state.ClaudeStateError, match="No valid pending"):
        claude_state.confirm_restart(app, owner_id="owner1", precondition_check=lambda: None)


def test_disarm_and_stop_are_immediate_and_unconditional(app):
    claude_state.arm(app, owner_id="owner1")
    claude_state.disarm(app)
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF

    claude_state.arm(app, owner_id="owner1")
    claude_state.stop(app)
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


# ---------------------------------------------------------------------------
# claude_state.evaluate_drawdown -- the equity/high-water-mark model
# (review, 2026-08-26): drawdown is measured against a persisted
# high-water-mark of TOTAL equity (capital basis + realised + unrealised),
# not just closed-position realised P&L against a fixed basis.
# ---------------------------------------------------------------------------

MAX_DD = Decimal("20.00")


def test_hwm_seeds_at_capital_basis_on_first_call(app):
    result = claude_state.evaluate_drawdown(
        app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD
    )
    assert result["high_water_equity_usd"] == Decimal("1000")
    assert result["drawdown_pct"] == Decimal("0.00")
    assert result["breached"] is False


def test_unrealised_open_position_loss_19_99_does_not_latch(app):
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    # equity dropped to 800.10 purely from an open-position mark-to-market
    # loss (no realised P&L, no BUY/SELL involved) -- 19.99% of the 1000 HWM
    result = claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("800.10"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    assert result["drawdown_pct"] == Decimal("19.99")
    assert result["breached"] is False


def test_unrealised_open_position_loss_exactly_20_latches(app):
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    result = claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("800.00"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    assert result["drawdown_pct"] == Decimal("20.00")
    assert result["breached"] is True


def test_hwm_only_moves_upward_during_normal_operation(app):
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    r2 = claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1100"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    assert r2["high_water_equity_usd"] == Decimal("1100")
    r3 = claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1050"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    # HWM must NOT drop back to 1050 just because equity did
    assert r3["high_water_equity_usd"] == Decimal("1100")
    assert r3["drawdown_pct"] == quantize_pct((Decimal("1100") - Decimal("1050")) / Decimal("1100") * 100)


def test_hwm_persists_across_reload_and_restart(app):
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1200"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    reloaded = claude_state.load_state(app)
    assert Decimal(reloaded["high_water_equity_usd"]) == Decimal("1200")
    claude_state.reset_on_startup(app)
    after_restart = claude_state.load_state(app)
    assert Decimal(after_restart["high_water_equity_usd"]) == Decimal("1200")


def test_no_currency_artifact_from_repricing_historical_realised_pnl(app):
    # record_realized_pnl takes an already-USD-priced delta -- adding two
    # trades priced at different (simulated) historical rates must simply
    # sum, never get re-derived from a later/current price.
    claude_state.record_realized_pnl(app, pnl_usd=Decimal("50"))   # trade #1, priced at its own close time
    claude_state.record_realized_pnl(app, pnl_usd=Decimal("-20"))  # trade #2, priced at a different close time
    state = claude_state.load_state(app)
    assert Decimal(state["cumulative_realized_pnl_usd"]) == Decimal("30")


def test_reset_high_water_to_current_establishes_fresh_baseline(app):
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("1000"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("800"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    claude_state.reset_high_water_to_current(app, current_equity_usd=Decimal("800"))
    result = claude_state.evaluate_drawdown(app, current_equity_usd=Decimal("800"), capital_basis_usd=Decimal("1000"), max_drawdown_pct=MAX_DD)
    # immediately after a reset, current == new HWM -- 0% drawdown, not 20%
    assert result["drawdown_pct"] == Decimal("0.00")
    assert result["high_water_equity_usd"] == Decimal("800")


def test_force_off_never_touches_halted_drawdown(app):
    claude_state.arm(app, owner_id="owner1")
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("25.00"), drawdown_usd=Decimal("250"))
    state = claude_state.force_off(app, reason="signer not ready")
    assert state["operating_state"] == claude_state.OFF
    assert state["halted_drawdown"] is True  # force_off must never clear a latch
    assert state["last_forced_off_reason"] == "signer not ready"


def test_force_off_active_transition_out_of_armed(app):
    claude_state.arm(app, owner_id="owner1")
    assert claude_state.load_state(app)["operating_state"] == claude_state.ARMED
    claude_state.force_off(app, reason="risk config invalid")
    assert claude_state.load_state(app)["operating_state"] == claude_state.OFF


def test_is_armed_false_when_halted_even_if_operating_state_stale(app):
    state = claude_state.arm(app, owner_id="owner1")
    assert claude_state.is_armed(state) is True
    claude_state.latch_drawdown(app, drawdown_pct=Decimal("20.00"), drawdown_usd=Decimal("200"))
    reloaded = claude_state.load_state(app)
    assert claude_state.is_armed(reloaded) is False


# ---------------------------------------------------------------------------
# Structural: exactly one authoritative command path / state machine.
# Pure text inspection -- no import of learnerbot required.
# ---------------------------------------------------------------------------


def test_only_telegram_control_patch_assigns_ui_handle_update():
    hits = []
    for path in BOT_DIR.glob("*.py"):
        if path.name == "telegram_control_patch.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "_ui.handle_update =" in text or "telegram_ui.handle_update =" in text:
            hits.append(path.name)
    assert hits == [], f"more than one module assigns telegram_ui.handle_update: {hits}"


def test_only_telegram_control_patch_calls_state_mutating_owner_functions():
    """claude_state.arm()/disarm()/stop()/issue_restart_challenge()/
    confirm_restart() must only ever be ACTUALLY CALLED from
    telegram_control_patch.py (the owner-gated handler) or claude_state.py
    itself -- proving no other module, and therefore no AI/mailbox/
    internal/scheduler code path anywhere in this bot, can reach them.
    AST-based (real Call nodes), not substring matching -- a docstring or
    comment mentioning e.g. "claude_state.confirm_restart()" in prose must
    not false-positive here."""
    import ast

    mutators = {"arm", "disarm", "stop", "issue_restart_challenge", "confirm_restart"}
    offenders = []
    for path in BOT_DIR.glob("*.py"):
        if path.name in {"telegram_control_patch.py", "claude_state.py"} or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in mutators
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "claude_state"
            ):
                offenders.append((path.name, node.func.attr, node.lineno))
    assert offenders == [], f"unexpected call site(s) for owner-gated state mutation: {offenders}"


def test_old_handler_fully_removed_from_execution_guard():
    # telegram_control_patch.py itself (imports learnerbot at module level) is
    # exercised in test_claude_execution_and_telegram.py, which asserts its
    # COMMANDS set directly. This is the platform-independent half: the old
    # module must no longer define or install a handler at all.
    guard_text = (BOT_DIR / "solana_execution_risk_patch.py").read_text(encoding="utf-8")
    assert "def handle_update" not in guard_text
    assert "_ui.handle_update" not in guard_text
