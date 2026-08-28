from __future__ import annotations

"""Presentation-only learner Telegram format completion.

NewPoll45 sections are labelled as Open Position X of Y and every position uses
complete token/pool/SOL/USD context. Confirmed SELL notices get an explicit sale
result block, then flow through the existing pool-context notifier so the same
market context is appended. No trading decisions or execution paths are changed.
"""

import re
from contextlib import closing
from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_sibot as _sol
from . import telegram_learner_complete_market_context_patch as _complete  # noqa: F401
from . import telegram_learner_position_update_patch as _position

_PREV_POSITION_SECTION = _position._position_section
_PREV_NOTIFY = _live._notify


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _position_for_sale(app, tid: str, message: str) -> dict:
    match = re.search(r"TX:\s*<code>([^<]+)</code>", str(message or ""), flags=re.I)
    if not match:
        return {}
    sig = match.group(1).strip()
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                """SELECT * FROM positions
                   WHERE telegram_id=? AND exit_signature=?
                   ORDER BY updated_at DESC LIMIT 1""",
                (str(tid), sig),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _open_count(app, tid: str) -> int:
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
                (str(tid),),
            ).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def _sol_usd_pair(app, value: Decimal, *, signed: bool = False) -> str:
    value = _d(value)
    sol_price = _position._sol_usd(app)
    sol_text = f"{value:+,.9f}" if signed else f"{value:,.9f}"
    if sol_price > 0:
        return f"{sol_text} SOL (≈ {_position._usd_text(value * sol_price)})"
    return f"{sol_text} SOL (USD unavailable)"


def position_section_newpoll_full(app, position: dict, index: int, total: int, cfg: dict, sol_price: Decimal):
    section, pid, snapshot = _PREV_POSITION_SECTION(app, position, index, total, cfg, sol_price)
    old = f"<b>Open Position {index} of {total}</b>"
    new = (
        f"📡 <b>NewPoll45 • Open Position {index} of {total}</b>\n"
        f"📚 Total open positions: <b>{total}</b>"
    )
    if old in section:
        section = section.replace(old, new, 1)
    else:
        section = new + "\n" + section
    return section, pid, snapshot


def notify_with_full_sale_result(app, tid, text):
    message = str(text or "")
    if "Solana LIVE SELL confirmed" not in message:
        return _PREV_NOTIFY(app, tid, message)

    position = _position_for_sale(app, str(tid), message)
    if position:
        status = str(position.get("status") or "UNKNOWN").upper()
        remaining_raw = str(position.get("token_amount_raw") or "0")
        realised = _d(position.get("realised_net_sol"), 0)
        pid = str(position.get("position_id") or "")
        open_count = _open_count(app, str(tid))

        received = None
        net_portion = None
        m = re.search(r"Received wallet delta:\s*<b>([+\-0-9.]+)\s+SOL</b>", message, flags=re.I)
        if m:
            received = _d(m.group(1), 0)
        m = re.search(r"Net on sold portion:\s*<b>([+\-0-9.]+)\s+SOL</b>", message, flags=re.I)
        if m:
            net_portion = _d(m.group(1), 0)

        lines = [
            "",
            "📦 <b>FULL SALE RESULT</b>",
            f"🧾 Position: <code>{pid}</code>",
            f"📌 Position status: <b>{status}</b>",
            f"📚 Open positions remaining: <b>{open_count}</b>",
            f"🪙 Remaining raw balance recorded: <code>{remaining_raw}</code>",
        ]
        if received is not None:
            lines.append(f"💰 Sale received: <b>{_sol_usd_pair(app, received)}</b>")
        if net_portion is not None:
            lines.append(f"📈 Net on this sale: <b>{_sol_usd_pair(app, net_portion, signed=True)}</b>")
        lines.append(f"✅ Total realised P&L: <b>{_sol_usd_pair(app, realised, signed=True)}</b>")
        message = message.rstrip() + "\n" + "\n".join(lines)

    # _PREV_NOTIFY is the existing pool-context wrapper. It appends the complete
    # token name/symbol, pool/Dex Viewer, pool open/current/change and SOL/USD data.
    return _PREV_NOTIFY(app, tid, message)


def install() -> None:
    if getattr(_position, "_learner_newpoll_full_format_installed", False):
        return
    _position._position_section = position_section_newpoll_full
    _live._notify = notify_with_full_sale_result
    _position._learner_newpoll_full_format_installed = True
    print(
        "[learner-newpoll-full-format] active=true newpoll45=true open_position_x_of_y=true "
        "sale_full_result=true complete_context=true trading_logic=unchanged",
        flush=True,
    )


install()
