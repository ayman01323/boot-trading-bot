"""The one authoritative Claude-bot Telegram command router.

Wraps learnerbot.telegram_ui.handle_update (the existing single-poller
routing system every Telegram update already passes through) and intercepts
exactly six commands before falling through to the previous handler for
everything else. Consolidates and replaces the older /sibot1riskresume /
/sibot1riskstatus handler that used to live inside
solana_execution_risk_patch.py -- there is now exactly one place that can
install a Claude command handler onto _ui.handle_update, so two competing
state machines cannot exist.

Commands (see claude_state.py for the state machine, risk_engine_guard.py
for the risk numbers):
  /claude_status              read-only
  /claude_arm_live CONFIRM    OFF -> ARMED
  /claude_disarm               -> OFF, immediate
  /claude_stop                 -> STOPPING -> OFF, immediate
  /claude_restart_request      issues a short-lived restart challenge (only
                                valid while HALTED_DRAWDOWN)
  /claude_restart_confirm CONFIRM
                                consumes the challenge, rechecks
                                preconditions, clears HALTED_DRAWDOWN

Owner authentication: the sender's Telegram id must exactly match
CLAUDE_BOT_WALLET_OWNER_ID, read from runtime configuration -- never
hard-coded here. A non-owner sender gets a flat refusal for every one of
these six commands, no exceptions. This handler only ever fires from a real
incoming Telegram update delivered through the poller; nothing in this
module is reachable from a mailbox message, an API call, a scheduler, or
process startup -- there is no code path into these functions except
handle_update() being called with a real update dict.
"""

from __future__ import annotations

import os

from learnerbot import telegram as _telegram
from learnerbot import telegram_ui as _ui

import claude_state
import risk_engine_guard
import solana_execution_risk_patch as _guard

_PREV_HANDLE_UPDATE = _ui.handle_update

COMMANDS = {
    "/claude_status",
    "/claude_arm_live",
    "/claude_disarm",
    "/claude_stop",
    "/claude_restart_request",
    "/claude_restart_confirm",
}


def _owner_id() -> str:
    return os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()


def _send(app, chat_id: str, text: str) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or not str(chat_id).strip():
        return
    try:
        _telegram.send_message(token, str(chat_id), text, parse_mode="HTML", protect_content=True)
    except Exception as exc:
        print("[claude-telegram-control]", type(exc).__name__, str(exc)[:240])


def _status_text(app) -> str:
    state = claude_state.load_state(app)
    try:
        limits = risk_engine_guard.RiskLimits.load()
        risk_line = (
            f"Capital basis: ${limits.capital_basis_usd:.2f}\n"
            f"Per-position ceiling: {limits.max_position_pct:.2f}% (${limits.max_position_usd:.2f})\n"
            f"Aggregate exposure ceiling: {limits.max_total_exposure_pct:.2f}% (${limits.max_total_exposure_usd:.2f})\n"
            f"Max open positions: {limits.max_open_positions}\n"
        )
        risk_ok = True
    except risk_engine_guard.RiskGuardConfigError as exc:
        risk_line = f"Risk config: INVALID — {exc}\n"
        risk_ok = False

    try:
        import signing_interface

        signer_status = signing_interface.get_signer_status(app)
        signer_line = f"Signer ready: {str(signer_status.ready).lower()} ({signer_status.reason})\n"
    except Exception as exc:  # noqa: BLE001
        signer_line = f"Signer status: unavailable ({type(exc).__name__})\n"

    chains = os.environ.get("AUTHORISED_CHAINS", "").strip() or "(none)"

    exposure_line = ""
    if risk_ok:
        try:
            owner_id = _owner_id()
            snapshot = _guard.position_snapshot(app, owner_id)
            exposure_pct = limits.position_pct(snapshot["exposure_usd"])
            # Same authoritative call every other caller uses (guard, monitor,
            # tests) -- if this read reveals a breach, it latches + alerts here too.
            result = _guard._check_and_latch_drawdown(
                app, owner_id, limits=limits, open_positions=snapshot["open_positions"]
            )
            exposure_line = (
                f"Open positions: {snapshot['open_positions']} / {limits.max_open_positions}\n"
                f"Aggregate exposure: {exposure_pct:.2f}% (ceiling {limits.max_total_exposure_pct:.2f}%)\n"
                f"High-water equity: ${result['high_water_equity_usd']:.2f}\n"
                f"Current equity: ${result['current_equity_usd']:.2f}\n"
                f"Current drawdown: {result['drawdown_pct']:.2f}% (latch at {limits.max_drawdown_pct:.2f}%)\n"
            )
        except Exception as exc:  # noqa: BLE001
            exposure_line = f"Position/exposure data unavailable: {type(exc).__name__}\n"

    halted = "🛑 HALTED_DRAWDOWN" if state.get("halted_drawdown") else "not latched"
    unpriced = state.get("unpriced_closed_position_ids") or {}
    unpriced_line = (
        f"⚠️ <b>{len(unpriced)} closed position(s) with no trustworthy close-time valuation</b> "
        f"— equity untrustworthy, ARM refused until manually reconciled\n"
        if unpriced else ""
    )
    return (
        "<b>🤖 CLAUDE TRADING BOT — STATUS</b>\n"
        f"Effective state: <b>{claude_state.effective_state(state)}</b>\n"
        f"Drawdown latch: <b>{halted}</b>\n"
        f"{unpriced_line}"
        f"{exposure_line}"
        f"{risk_line}"
        f"{signer_line}"
        f"Authorised chain(s): {chains}\n"
    )


def _require_owner(app, chat_id: str, sender_id: str) -> bool:
    owner_id = _owner_id()
    if not owner_id or str(sender_id) != owner_id:
        _send(app, chat_id, "❌ <b>Not authorised.</b> Only the configured wallet owner may control the Claude bot.")
        return False
    return True


def _handle_claude_command(app, chat_id: str, sender_id: str, cmd: str, parts: list[str]) -> None:
    if not _require_owner(app, chat_id, sender_id):
        return

    if cmd == "/claude_status":
        _send(app, chat_id, _status_text(app))
        return

    if cmd == "/claude_arm_live":
        if len(parts) != 2 or parts[1].upper() != "CONFIRM":
            _send(app, chat_id, "❌ To arm use exactly: <code>/claude_arm_live CONFIRM</code>")
            return
        # Same authoritative precondition check the periodic monitor and
        # restart-confirm use -- one function, not a third copy of the same
        # signer/risk/chain checks.
        reason = _guard.armed_health_check(app, sender_id)
        if reason:
            _send(app, chat_id, f"❌ <b>Arm refused.</b>\n<code>{reason}</code>")
            return
        try:
            claude_state.arm(app, owner_id=sender_id)
        except claude_state.ClaudeStateError as exc:
            _send(app, chat_id, f"❌ <b>Arm refused.</b>\n<code>{exc}</code>")
            return
        _send(app, chat_id, "✅ <b>ARMED.</b> LIVE entries permitted, subject to every existing risk/signer/chain/pool control.")
        return

    if cmd == "/claude_disarm":
        claude_state.disarm(app)
        _send(app, chat_id, "✅ <b>DISARMED</b> → OFF. No new LIVE entry authority.")
        return

    if cmd == "/claude_stop":
        claude_state.stop(app)
        _send(app, chat_id, "🛑 <b>STOPPED</b> → OFF. New entries blocked immediately. Existing positions remain exitable.")
        return

    if cmd == "/claude_restart_request":
        try:
            claude_state.issue_restart_challenge(app, owner_id=sender_id)
        except claude_state.ClaudeStateError as exc:
            _send(app, chat_id, f"ℹ️ {exc}")
            return
        _send(
            app,
            chat_id,
            "🔑 Restart challenge issued. Confirm within "
            f"{claude_state.RESTART_CHALLENGE_TTL_SECONDS // 60} minutes with: "
            "<code>/claude_restart_confirm CONFIRM</code>",
        )
        return

    if cmd == "/claude_restart_confirm":
        if len(parts) != 2 or parts[1].upper() != "CONFIRM":
            _send(app, chat_id, "❌ To confirm use exactly: <code>/claude_restart_confirm CONFIRM</code>")
            return
        try:
            claude_state.confirm_restart(
                app, owner_id=sender_id, precondition_check=lambda: _guard.restart_preconditions(app)
            )
        except (claude_state.ClaudeStateError, risk_engine_guard.RiskGuardConfigError, _guard.ExecutionGuardError) as exc:
            _send(app, chat_id, f"❌ <b>Restart not authorised.</b>\n<code>{exc}</code>")
            return
        # Establish the fresh high-water-mark baseline the owner instruction
        # requires -- the old (inflated, pre-drawdown) HWM must not linger
        # as a ceiling that makes the next tick look like a smaller
        # drawdown than it really is.
        try:
            limits = risk_engine_guard.RiskLimits.load()
            _guard.reset_equity_baseline_after_restart(app, sender_id, capital_basis_usd=limits.capital_basis_usd)
        except Exception as exc:  # noqa: BLE001
            print("[claude-restart-baseline-reset]", type(exc).__name__, str(exc)[:240])
        _send(
            app,
            chat_id,
            "✅ <b>HALTED_DRAWDOWN cleared.</b> A fresh drawdown baseline starts now.\n"
            "Operating state remains OFF — send <code>/claude_arm_live CONFIRM</code> to resume LIVE entries.",
        )
        return


def handle_update(app, update):
    message = update.get("message") or {}
    text = str(message.get("text") or "").strip()
    if not text:
        return _PREV_HANDLE_UPDATE(app, update)
    parts = text.split()
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in COMMANDS:
        return _PREV_HANDLE_UPDATE(app, update)

    chat_id = str((message.get("chat") or {}).get("id") or "")
    sender_id = str((message.get("from") or {}).get("id") or chat_id)
    _handle_claude_command(app, chat_id, sender_id, cmd, parts)


def install() -> None:
    if not getattr(_ui, "_claude_control_patch_installed", False):
        _ui.handle_update = handle_update
        _ui._claude_control_patch_installed = True
