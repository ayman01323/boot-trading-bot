from __future__ import annotations

import calendar
import csv
import html
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import sibot as _sibot
from . import solana_sibot as _sol
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from .config import load_chains
from .user_registry import is_master

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE = _ui.handle_update
_PENDING: dict[str, str] = {}
_INSTALLED = False


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _months_ago_timestamp(now_ts: int, months: int) -> int:
    """Return the same UTC wall-clock time N calendar months earlier."""
    months = max(1, int(months))
    dt = datetime.fromtimestamp(int(now_ts), tz=timezone.utc)
    absolute = dt.year * 12 + (dt.month - 1) - months
    year, month0 = divmod(absolute, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return int(dt.replace(year=year, month=month, day=day).timestamp())


def parse_period(spec: str, now_ts: int | None = None) -> dict:
    """Parse 7d / 2m style periods. Months are true calendar months."""
    text = str(spec or "7d").strip().lower().replace(" ", "")
    if len(text) < 2 or text[-1] not in {"d", "m"}:
        raise ValueError("Use a period such as 7d, 45d, 2m or 12m")
    try:
        n = int(text[:-1])
    except Exception as exc:
        raise ValueError("Period must be a whole number followed by d or m") from exc
    if n <= 0:
        raise ValueError("Period must be greater than zero")
    now = int(now_ts or time.time())
    if text[-1] == "d":
        if n > 3650:
            raise ValueError("Custom days must be between 1 and 3650")
        cutoff = now - n * 86400
        label = f"Last {n} day" + ("" if n == 1 else "s")
        unit = "days"
    else:
        if n > 120:
            raise ValueError("Custom months must be between 1 and 120")
        cutoff = _months_ago_timestamp(now, n)
        label = f"Last {n} calendar month" + ("" if n == 1 else "s")
        unit = "months"
    return {"spec": f"{n}{text[-1]}", "count": n, "unit": unit, "cutoff": cutoff, "now": now, "label": label}


def _chain_catalogue(app) -> dict[int, dict]:
    out = {}
    for c in load_chains(app, enabled_only=False):
        out[int(c.chain_id)] = {
            "chain_id": int(c.chain_id),
            "slug": str(c.slug),
            "name": str(c.name),
            "symbol": str(c.native_symbol or c.wrapped_base_symbol or "NATIVE"),
        }
    out[int(_sol.SOLANA_CHAIN_ID)] = {
        "chain_id": int(_sol.SOLANA_CHAIN_ID),
        "slug": "solana",
        "name": "Solana",
        "symbol": "SOL",
    }
    return out


def _route_rows(app, cutoff: int) -> list[dict]:
    """Closed EVM route/arbitrage AUTO executions, net of recorded profit fee."""
    path = Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"
    out = []
    for r in _csv_rows(path):
        status = str(r.get("status") or "").upper()
        if status not in {"SUCCESS", "SUCCESS_FEE_PENDING"}:
            continue
        try:
            ts = int(float(r.get("timestamp_epoch") or 0))
            cid = int(float(r.get("chain_id") or 0))
        except Exception:
            continue
        if ts < int(cutoff) or not str(r.get("realised_net_base") or "").strip():
            continue
        gross = _d(r.get("realised_net_base"), 0)
        fee = max(Decimal(0), _d(r.get("profit_fee_base"), 0))
        out.append({
            "telegram_id": str(r.get("telegram_id") or ""),
            "chain_id": cid,
            "chain_slug": str(r.get("chain_slug") or ""),
            "closed_at": ts,
            "pnl": gross - fee,
            "fee": fee,
            "source": "EVM Routes",
        })
    return out


def _evm_sibot_rows(app, cutoff: int) -> list[dict]:
    """Closed EVM SiBot LIVE positions, net of recorded platform profit fee."""
    try:
        with closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT telegram_id,chain_id,chain_slug,realised_net_native,
                          profit_fee_native,closed_at
                   FROM positions
                   WHERE status='CLOSED' AND mode='LIVE' AND closed_at>=?""",
                (int(cutoff),),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        gross = _d(r["realised_net_native"], 0)
        fee = max(Decimal(0), _d(r["profit_fee_native"], 0))
        out.append({
            "telegram_id": str(r["telegram_id"] or ""),
            "chain_id": int(r["chain_id"] or 0),
            "chain_slug": str(r["chain_slug"] or ""),
            "closed_at": int(r["closed_at"] or 0),
            "pnl": gross - fee,
            "fee": fee,
            "source": "EVM SiBot",
        })
    return out


def _solana_rows(app, cutoff: int) -> list[dict]:
    """Closed Solana SiBot LIVE positions using realised SOL cash P&L."""
    try:
        with closing(_sol.connect(app)) as conn:
            rows = conn.execute(
                """SELECT telegram_id,realised_net_sol,closed_at
                   FROM positions
                   WHERE status='CLOSED' AND mode='LIVE' AND closed_at>=?""",
                (int(cutoff),),
            ).fetchall()
    except Exception:
        return []
    return [{
        "telegram_id": str(r["telegram_id"] or ""),
        "chain_id": int(_sol.SOLANA_CHAIN_ID),
        "chain_slug": "solana",
        "closed_at": int(r["closed_at"] or 0),
        "pnl": _d(r["realised_net_sol"], 0),
        "fee": Decimal(0),
        "source": "Solana SiBot",
    } for r in rows]


def collect_all_users_pnl(app, period: str = "7d", now_ts: int | None = None) -> dict:
    meta = parse_period(period, now_ts=now_ts)
    catalogue = _chain_catalogue(app)
    rows = _route_rows(app, meta["cutoff"]) + _evm_sibot_rows(app, meta["cutoff"]) + _solana_rows(app, meta["cutoff"])
    grouped: dict[int, dict] = {}
    for r in rows:
        cid = int(r.get("chain_id") or 0)
        info = catalogue.get(cid, {
            "chain_id": cid,
            "slug": str(r.get("chain_slug") or cid),
            "name": str(r.get("chain_slug") or f"Chain {cid}").upper(),
            "symbol": "NATIVE",
        })
        if cid not in grouped:
            grouped[cid] = {
                **info,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "winning_pnl": Decimal(0),
                "losing_pnl": Decimal(0),
                "net_pnl": Decimal(0),
                "profit_fees": Decimal(0),
                "users": set(),
                "sources": defaultdict(int),
            }
        g = grouped[cid]
        pnl = _d(r.get("pnl"), 0)
        fee = max(Decimal(0), _d(r.get("fee"), 0))
        tid = str(r.get("telegram_id") or "").strip()
        if tid:
            g["users"].add(tid)
        g["sources"][str(r.get("source") or "Other")] += 1
        g["net_pnl"] += pnl
        g["profit_fees"] += fee
        if pnl > 0:
            g["wins"] += 1
            g["winning_pnl"] += pnl
        elif pnl < 0:
            g["losses"] += 1
            g["losing_pnl"] += -pnl
        else:
            g["breakeven"] += 1

    chains = []
    for g in grouped.values():
        g = dict(g)
        g["user_count"] = len(g.pop("users"))
        g["sources"] = dict(g["sources"])
        g["trades"] = int(g["wins"] + g["losses"] + g["breakeven"])
        decided = int(g["wins"] + g["losses"])
        g["win_rate"] = (Decimal(g["wins"]) * Decimal(100) / Decimal(decided)) if decided else Decimal(0)
        chains.append(g)
    chains.sort(key=lambda x: (str(x.get("name") or "").lower(), int(x.get("chain_id") or 0)))
    return {
        "period": meta,
        "chains": chains,
        "trade_count": sum(int(c["trades"]) for c in chains),
        "user_count": len({str(r.get("telegram_id") or "") for r in rows if str(r.get("telegram_id") or "").strip()}),
    }


def _fmt_amount(value: Decimal) -> str:
    v = _d(value, 0)
    av = abs(v)
    if av >= Decimal("100"):
        return f"{v:,.2f}"
    if av >= Decimal("1"):
        return f"{v:,.4f}"
    return f"{v:,.8f}"


def report_text(app, period: str = "7d", now_ts: int | None = None) -> str:
    report = collect_all_users_pnl(app, period=period, now_ts=now_ts)
    p = report["period"]
    cutoff = datetime.fromtimestamp(p["cutoff"], tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")
    lines = [
        "<b>📊 ALL USERS — REALISED WIN / LOSS REPORT</b>",
        "━━━━━━━━━━━━",
        f"Period: <b>{html.escape(p['label'])}</b>",
        f"From: <code>{html.escape(cutoff)}</code>",
        f"Users with closed trades: <b>{report['user_count']}</b> • Closed trades: <b>{report['trade_count']}</b>",
        "",
    ]
    if not report["chains"]:
        lines += [
            "No closed LIVE bot trades were recorded in this period.",
            "",
        ]
    for c in report["chains"]:
        symbol = html.escape(str(c["symbol"]))
        lines += [
            f"<b>🌐 {html.escape(str(c['name']))}</b>",
            f"✅ Wins <b>{c['wins']}</b> • ❌ Losses <b>{c['losses']}</b> • ➖ B/E <b>{c['breakeven']}</b> • Win rate <b>{c['win_rate']:.1f}%</b>",
            f"🟢 Winning P&amp;L: <b>+{html.escape(_fmt_amount(c['winning_pnl']))} {symbol}</b>",
            f"🔴 Losing P&amp;L: <b>-{html.escape(_fmt_amount(c['losing_pnl']))} {symbol}</b>",
            f"{'🟢' if c['net_pnl'] > 0 else '🔴' if c['net_pnl'] < 0 else '⚪'} Net realised: <b>{html.escape(_fmt_amount(c['net_pnl']))} {symbol}</b>",
            f"👥 Users: <b>{c['user_count']}</b> • Trades: <b>{c['trades']}</b>",
        ]
        if c["profit_fees"] > 0:
            lines.append(f"💼 Recorded profit fees: <b>{html.escape(_fmt_amount(c['profit_fees']))} {symbol}</b>")
        source_text = " • ".join(f"{html.escape(k)} {v}" for k, v in sorted(c["sources"].items()))
        if source_text:
            lines.append(f"Sources: {source_text}")
        lines.append("")
    lines += [
        "<i>Closed LIVE bot trades only. EVM route and EVM SiBot figures are shown after recorded platform profit fees; Solana uses realised SOL cash P&amp;L. Open/unrealised positions are excluded. Wrapped/native base assets are aggregated 1:1 on the same EVM chain.</i>",
    ]
    return "\n".join(lines)


def period_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "7 days", "callback_data": "allpnl:p:7d"},
            {"text": "14 days", "callback_data": "allpnl:p:14d"},
            {"text": "30 days", "callback_data": "allpnl:p:30d"},
        ],
        [
            {"text": "2 months", "callback_data": "allpnl:p:2m"},
            {"text": "3 months", "callback_data": "allpnl:p:3m"},
            {"text": "6 months", "callback_data": "allpnl:p:6m"},
        ],
        [
            {"text": "12 months", "callback_data": "allpnl:p:12m"},
            {"text": "✍️ Custom", "callback_data": "allpnl:custom"},
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def custom_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "Custom days", "callback_data": "allpnl:custom:days"},
            {"text": "Custom months", "callback_data": "allpnl:custom:months"},
        ],
        [{"text": "⬅️ Back", "callback_data": "menu:allpnl"}],
    ]}


def menu_keyboard(app=None, chat_id=None):
    kb = _PREV_MENU(app, chat_id)
    rows = [list(r) for r in (kb.get("inline_keyboard") or [])]
    if app is not None and chat_id is not None and is_master(app.csv_dir, chat_id):
        if not any(any(str(b.get("callback_data") or "") == "menu:allpnl" for b in row) for row in rows):
            rows.append([{"text": "📊 All Users Win / Loss", "callback_data": "menu:allpnl"}])
    return {"inline_keyboard": rows}


def _render(app, tid, text, keyboard, cb=None):
    _sibot_ui._render(app, tid, text, keyboard, cb)


def _master_or_reject(app, tid, cb=None) -> bool:
    if is_master(app.csv_dir, tid):
        return True
    try:
        if cb:
            _ui.answer_callback_query(app.telegram_bot_token, cb.get("id"), "MASTER only")
        else:
            _ui._send(app, tid, "❌ <b>MASTER only</b>", _ui.back_keyboard())
    except Exception:
        pass
    return False


def _show_custom_choice(app, tid, cb=None):
    _render(app, tid, "\n".join([
        "<b>✍️ Custom all-users P&amp;L period</b>",
        "━━━━━━━━━━━━",
        "Choose whether you want to enter a number of <b>days</b> or <b>calendar months</b>.",
    ]), custom_keyboard(), cb)


def _prompt_custom(app, tid, kind: str, cb=None):
    _PENDING[str(tid)] = kind
    if kind == "days":
        line = "Send a whole number of days, for example <code>45</code>. Allowed: 1–3650 days."
    else:
        line = "Send a whole number of calendar months, for example <code>4</code>. Allowed: 1–120 months."
    _render(app, tid, "\n".join([
        f"<b>✍️ Custom {html.escape(kind)}</b>",
        "━━━━━━━━━━━━",
        line,
        "Send <code>/cancel</code> to cancel.",
    ]), {"inline_keyboard": [[{"text": "Cancel", "callback_data": "menu:allpnl"}]]}, cb)


def _handle_custom_message(app, tid, text: str) -> bool:
    kind = _PENDING.get(str(tid))
    if kind not in {"days", "months"}:
        return False
    if str(text).strip().lower().split(maxsplit=1)[0].split("@", 1)[0] == "/cancel":
        _PENDING.pop(str(tid), None)
        _ui._send(app, tid, report_text(app, "7d"), period_keyboard())
        return True
    try:
        n = int(str(text).strip())
        spec = f"{n}{'d' if kind == 'days' else 'm'}"
        parse_period(spec)
    except Exception as exc:
        _ui._send(app, tid, f"❌ <b>Invalid period</b>\n<code>{html.escape(str(exc))}</code>", {"inline_keyboard": [[{"text": "Cancel", "callback_data": "menu:allpnl"}]]})
        return True
    _PENDING.pop(str(tid), None)
    _ui._send(app, tid, report_text(app, spec), period_keyboard())
    return True


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "menu:allpnl" or data.startswith("allpnl:"):
            if tid is None or not _master_or_reject(app, tid, cb):
                return
            try:
                _ui.answer_callback_query(app.telegram_bot_token, cb.get("id"), "")
            except Exception:
                pass
            try:
                if data == "menu:allpnl":
                    _PENDING.pop(str(tid), None)
                    _render(app, tid, report_text(app, "7d"), period_keyboard(), cb)
                elif data.startswith("allpnl:p:"):
                    spec = data.rsplit(":", 1)[-1]
                    _PENDING.pop(str(tid), None)
                    _render(app, tid, report_text(app, spec), period_keyboard(), cb)
                elif data == "allpnl:custom":
                    _show_custom_choice(app, tid, cb)
                elif data == "allpnl:custom:days":
                    _prompt_custom(app, tid, "days", cb)
                elif data == "allpnl:custom:months":
                    _prompt_custom(app, tid, "months", cb)
            except Exception as exc:
                _render(app, tid, f"❌ <b>All-users P&amp;L report error</b>\n<code>{html.escape(str(exc)[:500])}</code>", period_keyboard(), cb)
            return

    msg = update.get("message") or {}
    tid = (msg.get("chat") or {}).get("id")
    text = str(msg.get("text") or "").strip()
    if tid is not None and _PENDING.get(str(tid)):
        if not _master_or_reject(app, tid):
            _PENDING.pop(str(tid), None)
            return
        if _handle_custom_message(app, tid, text):
            return
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd == "/allpnl":
            if not _master_or_reject(app, tid):
                return
            parts = text.split(maxsplit=1)
            spec = parts[1].strip() if len(parts) > 1 else "7d"
            try:
                _ui._send(app, tid, report_text(app, spec), period_keyboard())
            except Exception as exc:
                _ui._send(app, tid, f"❌ <b>Use /allpnl 7d, /allpnl 45d, /allpnl 2m, etc.</b>\n<code>{html.escape(str(exc)[:400])}</code>", period_keyboard())
            return
    return _PREV_HANDLE(app, update)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _INSTALLED = True


install()
