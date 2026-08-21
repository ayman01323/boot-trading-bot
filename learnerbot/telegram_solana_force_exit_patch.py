from __future__ import annotations

import html
import os
import re

from . import solana_emergency_liquidity_unwind_patch as _unwind
from . import telegram_ui as _ui

_ORIGINAL_HANDLE_UPDATE = _ui.handle_update
_ORIGINAL_LIVE_NOTIFY = _unwind._live._notify
_EMERGENCY_PREFIX = "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>"


def _human_retry(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "") + f" ({seconds}s)"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} min ({seconds}s)"
    return f"{seconds}s"


def _format_emergency_liquidity_notice(text: str) -> str:
    raw = str(text or "")
    if not raw.startswith(_EMERGENCY_PREFIX):
        return raw

    reason = re.search(r"Reason: <code>(.*?)</code>", raw)
    position = re.search(r"Position: <code>(.*?)</code>", raw)
    ceiling = re.search(r"Hard impact\+slippage ceiling: <b>(.*?)</b>", raw)
    guard = re.search(r"Last guard: <code>(.*?)</code>", raw)
    retry = re.search(r"Automatic retry: <b>(\d+)s</b> \(liquidity attempt (\d+)\)\.", raw)
    if not all((reason, position, ceiling, guard, retry)):
        return raw

    retry_seconds = int(retry.group(1))
    attempt = retry.group(2)
    return (
        "🧯 <b>SOLANA EMERGENCY EXIT DEFERRED</b>\n"
        "⚠️ <b>Status:</b> Liquidity unsafe\n\n"
        "<b>Position</b>\n"
        f"• ID: <code>{position.group(1)}</code>\n"
        f"• Trigger: <code>{reason.group(1)}</code>\n\n"
        "<b>Safety checks</b>\n"
        f"• Maximum impact + slippage: <b>{ceiling.group(1)}</b>\n"
        "• Exit sizes tested: <b>100% → 75% → 50% → 25%</b>\n"
        "• Transaction broadcast: <b>NO</b>\n\n"
        "<b>Liquidity result</b>\n"
        "Jupiter priced every tested slice above the emergency ceiling.\n"
        f"<code>{guard.group(1)}</code>\n\n"
        "<b>Next action</b>\n"
        f"• Automatic retry: <b>{_human_retry(retry_seconds)}</b>\n"
        f"• Liquidity attempt: <b>{attempt}</b>\n\n"
        "🛡️ <b>Protection:</b> A near-100% price-impact quote is never bypassed automatically because it could realise essentially all remaining swap value as loss."
    )


def _live_notify_with_emergency_format(app, tid, text, *args, **kwargs):
    return _ORIGINAL_LIVE_NOTIFY(
        app,
        tid,
        _format_emergency_liquidity_notice(str(text or "")),
        *args,
        **kwargs,
    )


def handle_update(app, update):
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and text.startswith("/solanaforceexit"):
        try:
            if not _ui._auth(app, tid):
                raise ValueError("Not authorised")
            parts = text.split()
            if len(parts) != 3 or parts[2].upper() != "CONFIRM":
                raise ValueError(
                    "Use /solanaforceexit POSITION_ID CONFIRM -- this can realise most/all of the "
                    "remaining position value as loss. Only use it once you've decided the automatic "
                    "wait-for-liquidity retries are not going to resolve on their own."
                )
            position_id = parts[1]
            result = _unwind.force_close_live_position(app, tid, position_id)
            fraction = html.escape(str(result.get("liquidity_adaptive_fraction") or "1"))
            net = html.escape(str(result.get("net_sol") if result.get("net_sol") is not None else ""))
            closed = bool(result.get("closed"))
            _ui._send(
                app,
                tid,
                "✅ <b>Forced Solana exit executed</b>\n"
                f"Position: <code>{html.escape(position_id)}</code>\n"
                f"Fraction closed: <b>{fraction}</b>{' (fully closed)' if closed else ' (partial)'}\n"
                + (f"Realised net: <b>{net} SOL</b>\n" if net else "")
                + "This was an operator-confirmed manual override of the automatic 5% emergency ceiling.",
            )
        except Exception as exc:
            _ui._send(app, tid, f"❌ <b>Solana force exit</b>\n<code>{html.escape(str(exc)[:400])}</code>")
        return
    return _ORIGINAL_HANDLE_UPDATE(app, update)


def install():
    if not getattr(_unwind._live, "_emergency_liquidity_alert_format_installed", False):
        _unwind._live._notify = _live_notify_with_emergency_format
        _unwind._live._emergency_liquidity_alert_format_installed = True
    if getattr(_ui, "_solana_force_exit_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._solana_force_exit_patch_installed = True


install()

# This module is the final Telegram handler patch imported by learnerbot.__main__.
# Load the AI Council here so its MASTER/User menu and message handler stay outermost.
# The direct-provider layer replaces fragile local CLI calls with provider HTTP APIs;
# the friendly layer then presents one progress card + one final reply, and the rate
# limiter wraps that final start-question implementation.
from . import telegram_ai_council_patch as _ai_council  # noqa: E402,F401
os.environ.setdefault("AI_COUNCIL_RUNTIME_ENV", "/var/tmp/ai_council_runtime.env")
from . import ai_council_http_patch as _ai_council_http  # noqa: E402,F401
from . import telegram_ai_council_friendly_patch as _ai_council_friendly  # noqa: E402,F401
from . import telegram_ai_council_rate_limit_patch as _ai_council_limits  # noqa: E402,F401
