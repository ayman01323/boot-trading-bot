from __future__ import annotations

import html

from . import solana_emergency_liquidity_unwind_patch as _unwind
from . import telegram_ui as _ui

_ORIGINAL_HANDLE_UPDATE = _ui.handle_update


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
    if getattr(_ui, "_solana_force_exit_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._solana_force_exit_patch_installed = True


install()

# This module is the final Telegram handler patch imported by learnerbot.__main__.
# Load the AI Council here so its MASTER/User menu and message handler stay outermost
# and are not displaced by older compatibility layers. The friendly layer replaces
# message flooding with one progress card + one final reply; the rate limiter then
# wraps that final start-question implementation.
from . import telegram_ai_council_patch as _ai_council  # noqa: E402,F401
from . import telegram_ai_council_friendly_patch as _ai_council_friendly  # noqa: E402,F401
from . import telegram_ai_council_rate_limit_patch as _ai_council_limits  # noqa: E402,F401
