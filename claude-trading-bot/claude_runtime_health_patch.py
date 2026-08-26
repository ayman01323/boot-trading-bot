"""Composition-aware LIVE health gate for the isolated Claude bot.

Why this exists
---------------
Claude's own guards/router are installed before learnerbot's full patch chain.
The production learnerbot runtime intentionally adds audited outer wrappers later.
A direct identity assertion against the *final outermost* function therefore
produces false negatives even when Claude's guard/router is correctly composed.

This patch keeps the gate fail-closed while proving the right things:

* at Claude install time, before learnerbot's later wrappers, the Claude Solana
  guard, EVM deny guard, state machine, and Telegram router must all be the
  effective functions; otherwise startup fails immediately;
* a real authenticated owner Telegram command must have passed through Claude's
  command router in this process before ARM can succeed;
* the running learnerbot final-runtime integrity module must be loaded and every
  audited final hook must still pass;
* signer/identity, authorised chain, kill switch, quarantine, risk config, and
  unpriced-close accounting checks remain mandatory.

No financial limits are changed here. No command is auto-issued and no trading
state is changed by this module.
"""

from __future__ import annotations

import os
import sys
import time

from learnerbot import config as _learnerbot_config
from learnerbot import live_executor as _evm_executor
from learnerbot import solana_live_executor as _sol_executor
from learnerbot import telegram_ui as _ui

import claude_bot_quarantine
import claude_state as _state
import evm_execution_guard_patch as _evm_guard
import risk_engine_guard
import solana_execution_risk_patch as _guard
import telegram_control_patch as _router

_INSTALLED = False
_INSTALL_ATTESTED = False
_INSTALL_ATTESTATION: dict[str, bool] = {}

_ROUTER_DISPATCH_PROVEN = False
_ROUTER_DISPATCH_SENDER = ""
_ROUTER_DISPATCH_COMMAND = ""
_ROUTER_DISPATCH_AT = 0.0

_ORIGINAL_HANDLE_CLAUDE_COMMAND = _router._handle_claude_command
_ORIGINAL_ARMED_HEALTH_CHECK = _guard.armed_health_check


def _owner_id() -> str:
    return os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()


def _handle_claude_command_with_proof(app, chat_id: str, sender_id: str, cmd: str, parts: list[str]) -> None:
    """Record proof only for a real Claude command from the configured owner.

    This wrapper is called only from telegram_control_patch.handle_update().
    Recording happens before /claude_arm_live evaluates health so that the very
    ARM command being processed can prove the router is active.
    """
    global _ROUTER_DISPATCH_PROVEN
    global _ROUTER_DISPATCH_SENDER
    global _ROUTER_DISPATCH_COMMAND
    global _ROUTER_DISPATCH_AT

    if str(sender_id) == _owner_id() and str(cmd) in _router.COMMANDS:
        _ROUTER_DISPATCH_PROVEN = True
        _ROUTER_DISPATCH_SENDER = str(sender_id)
        _ROUTER_DISPATCH_COMMAND = str(cmd)
        _ROUTER_DISPATCH_AT = time.monotonic()

    return _ORIGINAL_HANDLE_CLAUDE_COMMAND(app, chat_id, sender_id, cmd, parts)


def _final_runtime_integrity_reason() -> str | None:
    module = sys.modules.get("learnerbot.final_runtime_integrity_patch")
    if module is None:
        return "learnerbot final runtime integrity module is not loaded"
    try:
        checks = dict(module.composition_checks())
    except Exception as exc:  # noqa: BLE001
        return f"learnerbot final runtime integrity unreadable: {type(exc).__name__}: {exc}"
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        return "learnerbot final runtime integrity mismatch: " + ", ".join(failed[:12])
    return None


def armed_health_check(app, telegram_id) -> str | None:
    """Authoritative composition-aware ARM/keep-ARMED health check."""
    try:
        risk_engine_guard.RiskLimits.load()
    except risk_engine_guard.RiskGuardConfigError as exc:
        return f"risk config invalid: {exc}"

    try:
        _guard.check_identity_and_signer(app, telegram_id)
    except _guard.ExecutionGuardError as exc:
        return f"signer/identity: {exc}"

    try:
        _guard.check_chain_authorised("solana")
    except _guard.ExecutionGuardError as exc:
        return f"chain: {exc}"

    try:
        op = app.operator_settings()
        engine_on = str(op.get("engine_enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
        if not engine_on:
            return "kill-switch active (operator_settings.engine_enabled=false)"
    except Exception as exc:  # noqa: BLE001
        return f"kill-switch state unreadable: {type(exc).__name__}: {exc}"

    if _learnerbot_config.load_dotenv is not claude_bot_quarantine._noop_load_dotenv:
        return "Claude quarantine is not intact: learnerbot.config.load_dotenv is not the no-op"

    if not _state._INSTALLED:
        return "Claude state machine (claude_state.install()) is not installed"

    if not _INSTALL_ATTESTED or not all(_INSTALL_ATTESTATION.values()):
        failed = [name for name, ok in _INSTALL_ATTESTATION.items() if not ok]
        return "Claude pre-chain guard/router attestation failed: " + ", ".join(failed or ["not attested"])

    if not getattr(_ui, "_claude_control_patch_installed", False):
        return "Claude Telegram router installation marker is missing"

    if not (
        _ROUTER_DISPATCH_PROVEN
        and _ROUTER_DISPATCH_SENDER == str(telegram_id)
        and _ROUTER_DISPATCH_AT > 0
    ):
        return "Claude Telegram router has not processed an authenticated owner command in this process"

    final_reason = _final_runtime_integrity_reason()
    if final_reason:
        return final_reason

    # The EVM guard remains deny-only for Claude. Its install marker plus the
    # pre-chain direct attestation above is mandatory even though learnerbot's
    # audited final BUY path adds its own pool/rug wrapper later.
    if not getattr(_evm_executor.LiveTrader, "_claude_evm_guard_installed", False):
        return "Claude EVM deny-guard installation marker is missing"

    if not getattr(_sol_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        return "Claude Solana risk-guard installation marker is missing"

    unpriced = (_state.load_state(app).get("unpriced_closed_position_ids") or {})
    if unpriced:
        return (
            f"{len(unpriced)} closed position(s) detected with no trustworthy close-time "
            "valuation -- equity cannot be trusted until manually reconciled"
        )

    return None


def install() -> None:
    """Attest the exact Claude functions before learnerbot adds outer wrappers."""
    global _INSTALLED
    global _INSTALL_ATTESTED
    global _INSTALL_ATTESTATION

    if _INSTALLED:
        return

    _INSTALL_ATTESTATION = {
        "solana_buy_guard": _sol_executor.SolanaLiveExecutor.buy is _guard._guarded_buy,
        "solana_sell_guard": _sol_executor.SolanaLiveExecutor.sell is _guard._guarded_sell,
        "evm_buy_deny": _evm_executor.LiveTrader.buy is _evm_guard._guarded_buy,
        "evm_sell_deny": _evm_executor.LiveTrader.sell is _evm_guard._guarded_sell,
        "evm_cycle_deny": _evm_executor.LiveTrader.execute_cycle is _evm_guard._guarded_execute_cycle,
        "evm_v3_cycle_deny": _evm_executor.LiveTrader.execute_v3_cycle is _evm_guard._guarded_execute_v3_cycle,
        "state_machine": bool(_state._INSTALLED),
        "telegram_router": _ui.handle_update is _router.handle_update,
        "quarantine": _learnerbot_config.load_dotenv is claude_bot_quarantine._noop_load_dotenv,
    }

    failed = [name for name, ok in _INSTALL_ATTESTATION.items() if not ok]
    if failed:
        raise RuntimeError("Claude pre-chain runtime attestation failed: " + ", ".join(failed))

    _INSTALL_ATTESTED = True

    # handle_update resolves this module global at call time, so replacing it
    # here preserves all later outer Telegram wrappers while letting a genuine
    # owner command attest that Claude's router was actually reached.
    _router._handle_claude_command = _handle_claude_command_with_proof

    # _guarded_buy, restart_preconditions, the Telegram router, and the periodic
    # monitor all resolve solana_execution_risk_patch.armed_health_check from the
    # module at runtime, so this remains one authoritative health function.
    _guard.armed_health_check = armed_health_check

    _INSTALLED = True
    print("[claude-runtime-health] install_attestation=OK router_proof=owner-command-required")
