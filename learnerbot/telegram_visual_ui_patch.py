from __future__ import annotations

import html
import re
from functools import wraps

from . import telegram_ui as _ui
from .user_registry import is_master

DIV = "━━━━━━━━━━━━━━━━━━━━"


def _plain(s: str) -> str:
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _compact_text(text: str, max_lines: int = 24) -> str:
    raw = str(text or "").replace("\r", "")
    lines = [x.strip() for x in raw.split("\n") if x.strip()]
    if not lines:
        return raw

    heading = lines[0]
    out = [heading, DIV]
    skipped = 0

    for line in lines[1:]:
        if len(out) >= max_lines:
            skipped += 1
            continue

        plain = _plain(line)
        if not plain:
            continue

        if line.startswith("<b>") and line.endswith("</b>"):
            if len(out) > 2 and out[-1] != "":
                out.append("")
            out.append(line)
            continue

        if line.startswith("• "):
            line = "▫️ " + line[2:]

        if len(plain) > 185 and not any(x in plain.lower() for x in ("warning", "important", "live", "profit", "security")):
            skipped += 1
            continue

        if ": " in plain and not line.startswith(("<code>", "<i>")):
            left, right = line.split(":", 1)
            if "<" not in left and len(_plain(left)) <= 34:
                line = f"▫️ <b>{html.escape(_plain(left))}</b>:{right}"

        out.append(line)

    if skipped:
        out += ["", "<i>📄 Main view simplified. Use the related detailed report/command only when needed.</i>"]

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def home_text() -> str:
    return "\n".join([
        "<b>⚡ BOOT Trading Dashboard</b>",
        DIV,
        "🟢 <b>BOT ONLINE</b>",
        "",
        "<b>QUICK ACCESS</b>",
        "🤖 <b>SiBot</b> — leader-copy strategy",
        "💰 <b>Capital & P&L</b> — wallet value and performance",
        "💱 <b>Trading</b> — live/manual controls",
        "⚡ <b>Auto Trade</b> — guarded automatic routes",
        "🛰 <b>Opportunities</b> — current market candidates",
        "",
        "<i>Select a section below. Technical detail is kept out of the main screens.</i>",
    ])


def menu_keyboard(app=None, chat_id=None):
    master = False
    try:
        master = bool(app is not None and chat_id is not None and is_master(app.csv_dir, chat_id))
    except Exception:
        pass

    rows = [
        [{"text": "🤖 SiBot", "callback_data": "menu:sibot"}, {"text": "💰 Capital & P&L", "callback_data": "menu:capital"}],
        [{"text": "🔐 Wallets", "callback_data": "menu:wallet"}, {"text": "💱 Trading", "callback_data": "menu:trading"}],
        [{"text": "⚡ Auto Trade", "callback_data": "menu:auto"}, {"text": "🛰 Opportunities", "callback_data": "menu:opportunities"}],
        [{"text": "🧺 Products", "callback_data": "menu:products"}, {"text": "🔥 Full Power", "callback_data": "menu:power"}],
        [{"text": "📡 Status", "callback_data": "menu:status"}, {"text": "❓ Help", "callback_data": "menu:help"}],
    ]
    if master:
        rows += [
            [{"text": "⚙️ Control", "callback_data": "menu:control"}, {"text": "🚀 Auto Deploy", "callback_data": "menu:autodeploy"}],
            [{"text": "🔔 Alerts", "callback_data": "menu:alerts"}, {"text": "🌐 Chains", "callback_data": "menu:chains"}],
            [{"text": "💰 Profit Research", "callback_data": "menu:profit"}, {"text": "🏆 Rankings", "callback_data": "menu:rankings"}],
            [{"text": "👥 Copy Top 20", "callback_data": "menu:copy20"}, {"text": "🚦 IN / OUT", "callback_data": "menu:signals"}],
            [{"text": "🔬 Behaviours", "callback_data": "menu:behaviours"}, {"text": "🧠 Strategies", "callback_data": "menu:strategies"}],
            [{"text": "🤖 Observed Wallets", "callback_data": "menu:wallets"}, {"text": "📥 Queue", "callback_data": "menu:queue"}],
            [{"text": "📊 Full Technical Report", "callback_data": "menu:report"}],
        ]
    return {"inline_keyboard": rows}


def _wrap_text_function(name: str, max_lines: int):
    fn = getattr(_ui, name, None)
    if not callable(fn):
        return

    @wraps(fn)
    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        if isinstance(result, tuple) and result and isinstance(result[0], str):
            return (_compact_text(result[0], max_lines), *result[1:])
        if isinstance(result, str):
            return _compact_text(result, max_lines)
        return result

    setattr(_ui, name, wrapped)


def install():
    if getattr(_ui, "_visual_ui_patch_installed", False):
        return

    for name, limit in {
        "chains_page": 20,
        "wallets_page": 28,
        "profit_page": 30,
        "behaviours_page": 30,
        "rankings_page": 30,
        "copy20_page": 32,
        "signals_page": 28,
        "help_page": 30,
        "control_page": 30,
        "behaviours_control_page": 22,
        "queue_page": 24,
        "wallet_page": 24,
        "auto_page": 24,
        "opportunities_page": 30,
        "power_page": 20,
        "products_page": 26,
        "trading_page": 24,
        "alerts_page": 22,
        "status_page": 24,
        "strategies_page": 30,
        "strategy_detail": 30,
    }.items():
        _wrap_text_function(name, limit)

    fn = getattr(_ui, "build_report_html", None)
    if callable(fn):
        @wraps(fn)
        def report_wrapper(*args, **kwargs):
            return _compact_text(fn(*args, **kwargs), 30)
        _ui.build_report_html = report_wrapper

    _ui.home_text = home_text
    _ui.menu_keyboard = menu_keyboard
    _ui._visual_ui_patch_installed = True


install()
