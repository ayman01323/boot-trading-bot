from __future__ import annotations

"""Complete learner NewPoll45 presentation for OPEN and reconciliation positions.

One bounded Telegram message is emitted per tracked LIVE position. OPEN positions
retain the full NewPoll45 market context. RECONCILE_REQUIRED positions are shown
as reconciliation/manual-exit records and are never described as current open
unrealised positions. Confirmed SELL notices receive a full sale-result block.
Presentation only: no trading decision, execution, safety gate or DB accounting
state is changed here.
"""

import html as _html
import re
from collections import defaultdict
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


def _plain_from_html(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2))}: {m.group(1)}",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return _html.unescape(value)


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


def _report_positions(app) -> list[dict]:
    """Report active LIVE positions plus quarantined rows awaiting reconciliation."""
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            rows = conn.execute(
                """SELECT * FROM positions
                   WHERE mode='LIVE' AND status IN ('OPEN','RECONCILE_REQUIRED')
                   ORDER BY telegram_id,
                            CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                            entry_ts,position_id"""
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


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
    status = str(position.get("status") or "UNKNOWN").upper()
    old = f"<b>Open Position {index} of {total}</b>"

    if status == "RECONCILE_REQUIRED":
        new = (
            f"🧾 <b>NewPoll45 • Reconciliation Position {index} of {total}</b>\n"
            f"⚠️ Status: <b>RECONCILE_REQUIRED</b>\n"
            "✅ This row is <b>not counted as an OPEN LIVE position</b>.\n"
            "🔄 On-chain wallet state no longer matches the stored position row; manual/external sale reconciliation is required.\n"
            "📌 Values labelled current/unrealised below are <b>last stored valuation only</b> — not final realised P&L."
        )
    else:
        new = (
            f"📡 <b>NewPoll45 • Open Position {index} of {total}</b>\n"
            f"📚 Total tracked LIVE reports: <b>{total}</b>"
        )
    if old in section:
        section = section.replace(old, new, 1)
    else:
        section = new + "\n" + section
    return section, pid, snapshot


def emit_newpoll_per_position(app) -> None:
    """Emit one bounded message per OPEN/reconciliation position with plain fallback."""
    positions = _report_positions(app)
    tracked_ids = {str(p.get("position_id") or "") for p in positions}
    for stale in list(_position._PREVIOUS):
        if stale not in tracked_ids:
            _position._PREVIOUS.pop(stale, None)
    if not positions:
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in positions:
        tid = str(row.get("telegram_id") or "")
        if tid:
            grouped[tid].append(row)

    cfg = _sol.settings(app)
    sol_price = _position._sol_usd(app)
    for tid, rows in grouped.items():
        total = len(rows)
        for index, position in enumerate(rows, start=1):
            section, pid, snapshot = _position._position_section(app, position, index, total, cfg, sol_price)
            status = str(position.get("status") or "UNKNOWN").upper()
            title = (
                "🧾 <b>LEARNER POSITION RECONCILIATION — NewPoll45</b>"
                if status == "RECONCILE_REQUIRED"
                else "📡 <b>LEARNER POSITION UPDATE — NewPoll45</b>"
            )
            text = "\n".join([
                title,
                "🔒 <b>LEARNER ONLY • GOOGLE TEST</b>",
                f"⏱ Report: <b>45s</b> • safety/exit monitor remains <b>{_html.escape(str(cfg.get('position_poll_seconds', '10')))}s</b>",
                "━━━━━━━━━━━━",
                "",
                section,
            ])

            delivered = False
            try:
                _position._tg.send_message(
                    app.telegram_bot_token,
                    tid,
                    text,
                    parse_mode="HTML",
                    protect_content=True,
                    disable_notification=True,
                )
                delivered = True
            except Exception as exc:
                try:
                    _position._tg.send_message(
                        app.telegram_bot_token,
                        tid,
                        _plain_from_html(text),
                        parse_mode=None,
                        protect_content=True,
                        disable_notification=True,
                    )
                    delivered = True
                    print(
                        "[learner-newpoll45] html_fallback=plain position=%s first_error=%s"
                        % (pid, type(exc).__name__),
                        flush=True,
                    )
                except Exception as fallback_exc:
                    print(
                        "[learner-newpoll45] send position=%s html=%s fallback=%s"
                        % (pid, type(exc).__name__, type(fallback_exc).__name__),
                        flush=True,
                    )

            if delivered and pid:
                _position._PREVIOUS[pid] = snapshot


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

    return _PREV_NOTIFY(app, tid, message)


def install() -> None:
    if getattr(_position, "_learner_newpoll_full_format_installed", False):
        return
    _position._position_section = position_section_newpoll_full
    _position._emit = emit_newpoll_per_position
    _live._notify = notify_with_full_sale_result
    _position._learner_newpoll_full_format_installed = True
    print(
        "[learner-newpoll-full-format] active=true newpoll45=true open_and_reconcile=true "
        "reconcile_not_unrealised=true per_position_delivery=true html_fallback=plain "
        "sale_full_result=true complete_context=true trading_logic=unchanged",
        flush=True,
    )


install()
