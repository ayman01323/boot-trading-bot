from __future__ import annotations

import csv
import html
import json
import time
from decimal import Decimal
from pathlib import Path

from . import telegram_sibot_patch as _legacy
from . import telegram_ui as _ui

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_MENU_KEYBOARD = _ui.menu_keyboard

DIV = "━━━━━━━━━━━━━━━━━━━━"
ENGINE_IDS = ("gpt", "gemini", "grok")
ENGINE_LABELS = {
    "gpt": "🤖 GPT",
    "gemini": "🔷 Gemini",
    "grok": "⚡ Grok",
}


def _safe_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _status_path(app) -> Path:
    return Path(app.data_dir) / "sibot1" / "status.json"


def _audit_path(app) -> Path:
    return Path(app.data_dir) / "sibot1" / "audit.ndjson"


def _load_status(app) -> dict:
    path = _status_path(app)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fresh(status: dict, seconds: int = 30) -> bool:
    try:
        return int(time.time()) - int(status.get("updated_epoch") or 0) <= seconds
    except Exception:
        return False


def _worker_state(status: dict, engine_id: str) -> tuple[str, str]:
    row = dict((status.get("workers") or {}).get(engine_id) or {})
    alive = row.get("alive") is True
    state = str(row.get("state") or "UNKNOWN").upper()
    if alive and state in {"READY", "HEALTH"}:
        return "🟢", state
    if alive:
        return "🟡", state
    return "🔴", state


def _score_rows(status: dict) -> list[dict]:
    rows = status.get("scoreboard") or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _engine_rows(status: dict, engine_id: str) -> list[dict]:
    return [row for row in _score_rows(status) if str(row.get("engine_id") or "").lower() == engine_id]


def _engine_totals(status: dict, engine_id: str) -> dict:
    rows = _engine_rows(status, engine_id)
    out = {
        "signals": 0,
        "shadow": 0,
        "blocks": 0,
        "entries": 0,
        "exits": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
    }
    mapping = {
        "signals": "signals",
        "shadow": "poolcheck_shadow",
        "blocks": "poolcheck_blocks",
        "entries": "paper_entries",
        "exits": "paper_exits",
        "wins": "paper_wins",
        "losses": "paper_losses",
        "errors": "errors",
    }
    for row in rows:
        for key, source in mapping.items():
            try:
                out[key] += int(row.get(source) or 0)
            except Exception:
                pass
    return out


def _pnl_by_chain(status: dict, engine_id: str) -> list[tuple[str, Decimal]]:
    out = []
    for row in _engine_rows(status, engine_id):
        chain = str(row.get("chain") or "unknown")
        out.append((chain, _safe_decimal(row.get("realised_pnl_quote"))))
    return sorted(out)


def _capital_accounts(status: dict, engine_id: str) -> list[tuple[str, dict]]:
    out = []
    for chain, payload in dict(status.get("capital") or {}).items():
        account = dict((payload or {}).get("accounts") or {}).get(engine_id)
        if isinstance(account, dict):
            out.append((str(chain), dict(account)))
    return sorted(out)


def menu_keyboard(app=None, chat_id=None):
    kb = _PREV_MENU_KEYBOARD(app, chat_id)
    rows = list(kb.get("inline_keyboard") or [])
    found = False
    for row in rows:
        for button in row:
            if str(button.get("callback_data") or "") == "menu:sibot":
                button["text"] = "🧠 SiBot 1"
                found = True
    if not found:
        insert_at = max(0, len(rows) - 1)
        rows.insert(insert_at, [{"text": "🧠 SiBot 1", "callback_data": "menu:sibot"}])
    return {"inline_keyboard": rows}


def sibot1_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🧠 Status", "callback_data": "sibot1:status"},
                {"text": "🏆 AI Scoreboard", "callback_data": "sibot1:scoreboard"},
            ],
            [
                {"text": "🤖 GPT", "callback_data": "sibot1:engine:gpt"},
                {"text": "🔷 Gemini", "callback_data": "sibot1:engine:gemini"},
                {"text": "⚡ Grok", "callback_data": "sibot1:engine:grok"},
            ],
            [
                {"text": "🧪 Paper P&L", "callback_data": "sibot1:pnl"},
                {"text": "🛡 PoolCheck", "callback_data": "sibot1:poolcheck"},
            ],
            [
                {"text": "🔬 Experiments", "callback_data": "sibot1:experiments"},
                {"text": "📚 Decisions & Audit", "callback_data": "sibot1:audit"},
            ],
            [
                {"text": "⚙️ Settings", "callback_data": "sibot1:settings"},
                {"text": "🔄 Refresh", "callback_data": "sibot1:status"},
            ],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]
    }


def _back_keyboard():
    return {"inline_keyboard": [[{"text": "⬅️ SiBot 1", "callback_data": "sibot1:status"}]]}


def main_page(app) -> str:
    status = _load_status(app)
    if not status:
        return "\n".join([
            "<b>🧠 SiBot 1 — AI Strategy Competition</b>",
            DIV,
            "",
            "🔴 Runtime status unavailable",
            "",
            "The SiBot 1 menu is isolated from the legacy LIVE executor.",
            "No LIVE control is exposed here.",
        ])

    fresh = _fresh(status)
    state = str(status.get("state") or "UNKNOWN").upper()
    mode = str(status.get("mode") or "UNKNOWN").upper()
    lines = [
        "<b>🧠 SiBot 1 — AI Strategy Competition</b>",
        DIV,
        "",
        f"{'🟢' if fresh and state == 'ACTIVE' else '🟠' if fresh else '🔴'} Controller  <b>{html.escape(state)}</b>",
        f"🧪 Mode  <b>{html.escape(mode)}</b>",
        f"💼 Open paper lots  <b>{int(status.get('open_lots') or 0)}</b>",
        "",
    ]
    for engine_id in ENGINE_IDS:
        icon, worker_state = _worker_state(status, engine_id)
        lines.append(f"{icon} <b>{ENGINE_LABELS[engine_id]}</b>  {html.escape(worker_state)}")
    lines += [
        "",
        "<b>🔒 HARD EXECUTION BOUNDARY</b>",
        f"Signing  <b>{'ON' if status.get('signer_attached') else 'OFF'}</b>",
        f"Broadcast  <b>{'ON' if status.get('broadcast_enabled') else 'OFF'}</b>",
        f"Private-key access  <b>{'ON' if status.get('wallet_private_key_access') else 'OFF'}</b>",
        "",
        "<i>This dashboard is SiBot 1 only. Legacy SiBot EVM/Solana LIVE controls are intentionally not shown here.</i>",
    ]
    return "\n".join(lines)


def scoreboard_page(app) -> str:
    status = _load_status(app)
    lines = ["<b>🏆 SiBot 1 — AI Scoreboard</b>", DIV, ""]
    if not status:
        return "\n".join(lines + ["🔴 Runtime status unavailable."])
    for engine_id in ENGINE_IDS:
        t = _engine_totals(status, engine_id)
        exits = t["exits"]
        win_rate = (100.0 * t["wins"] / exits) if exits else 0.0
        icon, worker_state = _worker_state(status, engine_id)
        lines += [
            f"{icon} <b>{ENGINE_LABELS[engine_id]}</b> — {html.escape(worker_state)}",
            f"Signals <b>{t['signals']}</b> • Entries <b>{t['entries']}</b> • Exits <b>{exits}</b>",
            f"Wins <b>{t['wins']}</b> • Losses <b>{t['losses']}</b> • Win rate <b>{win_rate:.1f}%</b>",
            f"Pool blocks <b>{t['blocks']}</b> • Errors <b>{t['errors']}</b>",
        ]
        pnls = _pnl_by_chain(status, engine_id)
        if pnls:
            lines.append("Paper realised P&L: " + " • ".join(
                f"{html.escape(chain)} <b>{pnl:+f}</b>" for chain, pnl in pnls
            ))
        else:
            lines.append("Paper realised P&L: <b>no closed evidence yet</b>")
        lines.append("")
    lines.append("<i>P&L is shown per chain because quote units across chains are not assumed to be directly comparable.</i>")
    return "\n".join(lines)


def engine_page(app, engine_id: str) -> str:
    if engine_id not in ENGINE_IDS:
        return "Unknown SiBot 1 engine."
    status = _load_status(app)
    lines = [f"<b>{ENGINE_LABELS[engine_id]} — SiBot 1 Engine</b>", DIV, ""]
    if not status:
        return "\n".join(lines + ["🔴 Runtime status unavailable."])
    icon, worker_state = _worker_state(status, engine_id)
    worker = dict((status.get("workers") or {}).get(engine_id) or {})
    t = _engine_totals(status, engine_id)
    lines += [
        f"{icon} Worker  <b>{html.escape(worker_state)}</b>",
        f"PID  <code>{html.escape(str(worker.get('pid') or '—'))}</code>",
        f"Signals  <b>{t['signals']}</b>",
        f"Paper entries/exits  <b>{t['entries']} / {t['exits']}</b>",
        f"Wins/losses  <b>{t['wins']} / {t['losses']}</b>",
        f"PoolCheck blocks  <b>{t['blocks']}</b>",
        f"Errors  <b>{t['errors']}</b>",
        "",
        "<b>Virtual capital</b>",
    ]
    accounts = _capital_accounts(status, engine_id)
    if not accounts:
        lines.append("No virtual capital allocation on the active chain pools.")
    for chain, account in accounts:
        lines.append(
            f"• {html.escape(chain)} — cash <b>{html.escape(str(account.get('cash') or '0'))}</b> • "
            f"invested <b>{html.escape(str(account.get('invested_cost') or '0'))}</b> • "
            f"realised <b>{html.escape(str(account.get('realised_pnl') or '0'))}</b>"
        )
    return "\n".join(lines)


def pnl_page(app) -> str:
    status = _load_status(app)
    lines = ["<b>🧪 SiBot 1 — Paper Capital &amp; P&amp;L</b>", DIV, ""]
    if not status:
        return "\n".join(lines + ["🔴 Runtime status unavailable."])
    capital = dict(status.get("capital") or {})
    if not capital:
        lines.append("No virtual capital snapshot yet.")
    for chain, payload in sorted(capital.items()):
        lines += [f"<b>🌐 {html.escape(str(chain))}</b>"]
        budget = str((payload or {}).get("physical_paper_budget") or "0")
        lines.append(f"Virtual pool budget  <b>{html.escape(budget)}</b>")
        for engine_id, account in sorted(dict((payload or {}).get("accounts") or {}).items()):
            label = ENGINE_LABELS.get(engine_id, engine_id)
            lines.append(
                f"• {label}: cash <b>{html.escape(str(account.get('cash') or '0'))}</b> • "
                f"reserved <b>{html.escape(str(account.get('reserved') or '0'))}</b> • "
                f"invested <b>{html.escape(str(account.get('invested_cost') or '0'))}</b> • "
                f"realised <b>{html.escape(str(account.get('realised_pnl') or '0'))}</b>"
            )
        lines.append("")
    lines += [
        f"Open paper lots  <b>{int(status.get('open_lots') or 0)}</b>",
        "",
        "<i>Virtual/paper balances only. This SiBot 1 runtime has no signer or transaction broadcast path.</i>",
    ]
    return "\n".join(lines)


def poolcheck_page(app) -> str:
    status = _load_status(app)
    lines = ["<b>🛡 SiBot 1 — PoolCheck</b>", DIV, ""]
    if not status:
        return "\n".join(lines + ["🔴 Runtime status unavailable."])
    total_shadow = 0
    total_blocks = 0
    for engine_id in ENGINE_IDS:
        t = _engine_totals(status, engine_id)
        total_shadow += t["shadow"]
        total_blocks += t["blocks"]
        lines.append(
            f"{ENGINE_LABELS[engine_id]} — shadow-only <b>{t['shadow']}</b> • hard/cooling blocks <b>{t['blocks']}</b>"
        )
    lines += [
        "",
        f"Total shadow-only decisions  <b>{total_shadow}</b>",
        f"Total blocked decisions  <b>{total_blocks}</b>",
        "",
        "<i>PoolCheck is mandatory before each paper entry and may force an emergency paper exit if an open-position check later hard-blocks the asset.</i>",
    ]
    return "\n".join(lines)


def experiments_page(app) -> str:
    status = _load_status(app)
    lines = ["<b>🔬 SiBot 1 — Experiments</b>", DIV, ""]
    if not status:
        return "\n".join(lines + ["🔴 Runtime status unavailable."])
    total_signals = total_entries = total_exits = 0
    for engine_id in ENGINE_IDS:
        t = _engine_totals(status, engine_id)
        total_signals += t["signals"]
        total_entries += t["entries"]
        total_exits += t["exits"]
    lines += [
        f"Market intents observed  <b>{total_signals}</b>",
        f"Paper entries  <b>{total_entries}</b>",
        f"Paper exits  <b>{total_exits}</b>",
        f"Open paper lots  <b>{int(status.get('open_lots') or 0)}</b>",
        "",
        "Each engine runs independently against shared read-only market evidence. Virtual capital and paper positions are centrally attributed so one engine cannot spend another engine's allocation.",
    ]
    return "\n".join(lines)


def audit_page(app) -> str:
    lines = ["<b>📚 SiBot 1 — Decisions &amp; Audit</b>", DIV, ""]
    path = _audit_path(app)
    if not path.exists():
        return "\n".join(lines + ["No SiBot 1 audit events recorded yet."])
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
    except Exception:
        return "\n".join(lines + ["Audit file is currently unreadable."])
    for item in raw:
        try:
            row = json.loads(item)
        except Exception:
            continue
        event = html.escape(str(row.get("event_type") or "EVENT"))
        engine = html.escape(str(row.get("engine_id") or "controller"))
        chain = html.escape(str(row.get("chain") or "runtime"))
        verdict = str(row.get("verdict") or "")
        detail = str(row.get("detail") or row.get("reason") or "")
        suffix = f" • {html.escape(verdict)}" if verdict else ""
        if detail:
            suffix += f" • <code>{html.escape(detail[:120])}</code>"
        lines.append(f"• <b>{event}</b> — {engine} / {chain}{suffix}")
    if len(lines) == 3:
        lines.append("No readable audit rows yet.")
    return "\n".join(lines)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def settings_page(app) -> str:
    status = _load_status(app)
    csv_dir = Path(app.csv_dir) / "sibot1"
    registry = _read_csv(csv_dir / "engine_registry.csv")
    runtime_rows = _read_csv(csv_dir / "runtime.csv")
    lines = ["<b>⚙️ SiBot 1 — Settings</b>", DIV, ""]
    lines += [
        f"Runtime mode  <b>{html.escape(str(status.get('mode') or 'UNKNOWN'))}</b>",
        "LIVE execution  <b>HARD DISABLED</b>",
        "Signing  <b>OFF</b>",
        "Broadcast  <b>OFF</b>",
        "",
        "<b>Engine registry</b>",
    ]
    if registry:
        for row in registry:
            engine = str(row.get("engine_id") or "unknown").lower()
            enabled = str(row.get("enabled") or "1").lower() in {"1", "true", "yes", "on"}
            lines.append(f"• {ENGINE_LABELS.get(engine, html.escape(engine))}: <b>{'ENABLED' if enabled else 'DISABLED'}</b>")
    else:
        lines.append("Default GPT / Gemini / Grok registry is in use.")
    lines += ["", "<b>Virtual capital pools</b>"]
    if runtime_rows:
        for row in runtime_rows:
            chain = html.escape(str(row.get("chain") or "unknown"))
            budget = html.escape(str(row.get("physical_paper_budget") or "0"))
            lines.append(f"• {chain}: budget <b>{budget}</b>")
    else:
        lines.append("Built-in virtual capital defaults are in use.")
    lines += [
        "",
        "<i>This Telegram menu is read-only. Changing SiBot 1 research configuration remains a controlled code/configuration change rather than a LIVE trading control.</i>",
    ]
    return "\n".join(lines)


def _answer(app, cb, text: str = "") -> None:
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _render(app, tid, text: str, keyboard: dict, cb=None) -> None:
    _legacy._render(app, tid, text, keyboard, cb)


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        # Stale legacy SiBot buttons are deliberately redirected to the new
        # SiBot 1-only menu instead of exposing old LIVE controls again.
        if data == "menu:sibot" or data.startswith("sibot1:") or data.startswith("sibot:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "sibot1:scoreboard":
                    text = scoreboard_page(app)
                    kb = _back_keyboard()
                elif data.startswith("sibot1:engine:"):
                    text = engine_page(app, data.rsplit(":", 1)[1])
                    kb = _back_keyboard()
                elif data == "sibot1:pnl":
                    text = pnl_page(app)
                    kb = _back_keyboard()
                elif data == "sibot1:poolcheck":
                    text = poolcheck_page(app)
                    kb = _back_keyboard()
                elif data == "sibot1:experiments":
                    text = experiments_page(app)
                    kb = _back_keyboard()
                elif data == "sibot1:audit":
                    text = audit_page(app)
                    kb = _back_keyboard()
                elif data == "sibot1:settings":
                    text = settings_page(app)
                    kb = _back_keyboard()
                else:
                    text = main_page(app)
                    kb = sibot1_keyboard()
                _render(app, tid, text, kb, cb)
            except Exception as exc:
                _render(
                    app,
                    tid,
                    "❌ <b>SiBot 1 menu error</b>\n<code>" + html.escape(str(exc)[:300]) + "</code>",
                    sibot1_keyboard(),
                    cb,
                )
            return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if tid is not None and cmd in {"/sibot", "/sibot1"}:
        if not _ui._auth(app, tid):
            return
        _ui._send(app, tid, main_page(app), sibot1_keyboard())
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_only_menu_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    # Replace globals used by the legacy /sibot renderer as a second line of
    # defence, while leaving its execution engine and explicit commands intact.
    _legacy.sibot_keyboard = lambda app, tid: sibot1_keyboard()
    _legacy.main_page = lambda app, tid: main_page(app)
    _ui._telegram_sibot1_only_menu_patch_installed = True
    print("[telegram-sibot1-menu] installed mode=sibot1-only legacy-live-buttons=hidden")


install()
