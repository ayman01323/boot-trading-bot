from __future__ import annotations

import copy
import html

from . import ai_master_control as _control
from . import telegram_ai_ops_patch as _ai
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from .ai_ops_status import fetch_ai_reviews, read_json, snapshot_for_display
from .user_registry import is_master

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update

_PROVIDER_LABELS = {
    "auto": "AUTO fallback",
    "gpt": "GPT",
    "gemini": "Gemini",
    "copilot": "Copilot",
    "claude": "Claude",
}


def _is_master(app, chat_id) -> bool:
    try:
        return bool(app is not None and chat_id is not None and is_master(app.csv_dir, chat_id))
    except Exception:
        return False


def _insert_ai_reports_button(rows: list[list[dict]]) -> list[list[dict]]:
    if any(any(str(b.get("callback_data") or "") == "menu:aiops" for b in row) for row in rows):
        return rows
    insert_at = len(rows)
    for i, row in enumerate(rows):
        callbacks = {str(b.get("callback_data") or "") for b in row}
        if "menu:report" in callbacks:
            insert_at = i
            break
    rows.insert(insert_at, [{"text": "🤖 AI Reports & Control", "callback_data": "menu:aiops"}])
    return rows


def menu_keyboard(app=None, chat_id=None):
    kb = copy.deepcopy(_PREV_MENU(app, chat_id))
    if not _is_master(app, chat_id):
        return kb
    rows = list(kb.get("inline_keyboard") or [])
    return {"inline_keyboard": _insert_ai_reports_button(rows)}


def _ai_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🎛 AI Master Control", "callback_data": "aiops:control"}],
            [{"text": "🖥 Claude VPS Access", "callback_data": "aiops:vps"}],
            [
                {"text": "🧪 Engineering Audit", "callback_data": "aiops:audit"},
                {"text": "🧠 Master Decisions", "callback_data": "aiops:decision"},
            ],
            [
                {"text": "🔬 Strategy Review", "callback_data": "aiops:strategy"},
                {"text": "📋 All AI Updates", "callback_data": "aiops:updates"},
            ],
            [
                {"text": "🔄 Refresh", "callback_data": "menu:aiops"},
                {"text": "⬅️ Main Menu", "callback_data": "menu:home"},
            ],
        ]
    }


def _control_keyboard(value: dict) -> dict:
    def mbtn(lane: str, provider: str):
        active = str(value.get(lane + "_master") or "auto") == provider
        label = _PROVIDER_LABELS[provider]
        return {"text": ("✅ " if active else "") + label, "callback_data": f"aicfg:master:{lane}:{provider}"}

    def cbtn(lane: str, mode: str, label: str):
        active = str(value.get(lane + "_cycle") or "scheduled") == mode
        return {"text": ("✅ " if active else "") + label, "callback_data": f"aicfg:cycle:{lane}:{mode}"}

    def toggle(lane: str):
        on = bool(value.get(lane + "_enabled", True))
        return {
            "text": f"{'🟢' if on else '🔴'} {lane.title()} {'ON' if on else 'OFF'}",
            "callback_data": f"aicfg:enabled:{lane}:{'off' if on else 'on'}",
        }

    return {"inline_keyboard": [
        [{"text": "🔬 STRATEGY MASTER", "callback_data": "aicfg:none"}],
        [mbtn("strategy", "auto"), mbtn("strategy", "gpt")],
        [mbtn("strategy", "gemini"), mbtn("strategy", "copilot")],
        [mbtn("strategy", "claude")],
        [cbtn("strategy", "scheduled", "⏱ Scheduled"), cbtn("strategy", "manual", "✋ Manual")],
        [toggle("strategy"), {"text": "▶️ Run Strategy now", "callback_data": "aicfg:run:strategy"}],
        [{"text": "🧪 ENGINEERING MASTER", "callback_data": "aicfg:none"}],
        [mbtn("engineering", "auto"), mbtn("engineering", "gpt")],
        [mbtn("engineering", "gemini"), mbtn("engineering", "copilot")],
        [mbtn("engineering", "claude")],
        [cbtn("engineering", "scheduled", "⏱ Scheduled"), cbtn("engineering", "manual", "✋ Manual")],
        [toggle("engineering"), {"text": "▶️ Run Engineering now", "callback_data": "aicfg:run:engineering"}],
        [{"text": "▶️ Run BOTH now", "callback_data": "aicfg:run:both"}],
        [{"text": "🖥 Claude VPS Access", "callback_data": "aiops:vps"}],
        [{"text": "⬅️ AI Reports", "callback_data": "menu:aiops"}],
    ]}


def _vps_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🔎 Inspect VPS", "callback_data": "aivps:run:inspect"},
            {"text": "🧪 Run VPS tests", "callback_data": "aivps:run:test"},
        ],
        [{"text": "🚀 Deploy current main", "callback_data": "aivps:confirm:deploy"}],
        [
            {"text": "🔄 Refresh", "callback_data": "aiops:vps"},
            {"text": "⬅️ AI Reports", "callback_data": "menu:aiops"},
        ],
    ]}


def _vps_confirm_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Confirm deploy CURRENT main", "callback_data": "aivps:run:deploy"}],
        [{"text": "❌ Cancel", "callback_data": "aiops:vps"}],
    ]}


def _master_label(value: dict, lane: str) -> str:
    provider = str(value.get(lane + "_master") or "auto")
    return _PROVIDER_LABELS.get(provider, provider.upper())


def _control_text(app) -> str:
    value = _control.load(app)
    return "\n".join([
        "<b>🎛 AI MASTER &amp; CYCLE CONTROL</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🔬 Strategy master: <b>{html.escape(_master_label(value, 'strategy'))}</b>",
        f"Strategy cycle: <b>{html.escape(str(value.get('strategy_cycle') or 'scheduled').upper())}</b> | "
        f"{'🟢 ON' if value.get('strategy_enabled', True) else '🔴 OFF'}",
        f"Strategy run request: <code>{int(value.get('strategy_run_nonce') or 0)}</code>",
        "",
        f"🧪 Engineering master: <b>{html.escape(_master_label(value, 'engineering'))}</b>",
        f"Engineering cycle: <b>{html.escape(str(value.get('engineering_cycle') or 'scheduled').upper())}</b> | "
        f"{'🟢 ON' if value.get('engineering_enabled', True) else '🔴 OFF'}",
        f"Engineering run request: <code>{int(value.get('engineering_run_nonce') or 0)}</code>",
        "",
        "<b>Resilience rule:</b> the selected master is tried first. If it fails, the next working AI takes over. "
        "One valid AI report is enough for the review cycle to continue; failed agents are reported to MASTER Telegram chats.",
        "",
        "<i>AI availability never disables the live trading engine. AI review/master decisions cannot bypass wallet, signing, simulation, liquidity, loss, capital or LIVE execution gates.</i>",
    ])


def _latest_vps_result() -> dict:
    root = _ai._repo_root()
    try:
        fetch_ai_reviews(root, timeout=12)
        value = read_json(root, "vps/claude/latest.json")
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return _control.load_vps_result()


def _vps_text(app) -> str:
    cfg = _control.load(app)
    result = _latest_vps_result()
    action = str(cfg.get("claude_vps_action") or "none").upper()
    nonce = int(cfg.get("claude_vps_action_nonce") or 0)
    status = str(result.get("status") or "WAITING").upper()
    last_action = str(result.get("action") or "none").upper()
    target = str(result.get("target_sha") or result.get("deployed_sha") or "")[:40]
    analysis = str(result.get("claude_analysis") or "").strip()
    if len(analysis) > 800:
        analysis = analysis[:800] + "…"
    lines = [
        "<b>🖥 CLAUDE VPS CONTROLLED ACCESS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "Claude is allowed to inspect sanitised VPS status/logs, run isolated tests, and deploy only the current GitHub <code>main</code> through the existing restricted root wrapper.",
        "",
        "🔒 No root shell | No arbitrary sudo | No wallet/private-key access | No safety-gate bypass",
        "",
        f"Pending/requested action: <b>{html.escape(action)}</b> #{nonce}",
        f"Last VPS result: <b>{html.escape(status)}</b> — {html.escape(last_action)}",
    ]
    if target:
        lines.append(f"Target/deployed SHA: <code>{html.escape(target)}</code>")
    if analysis:
        lines += ["", "<b>Claude VPS analysis:</b>", html.escape(analysis)]
    lines += [
        "",
        "<i>Deploy requires a second Telegram confirmation and can deploy only the current tested main SHA. The restricted VPS deployment wrapper still runs the full server test suite before restart.</i>",
    ]
    return "\n".join(lines)


def _mark(value):
    value = str(value or "WAITING").upper()
    if value in {"DONE", "WORKING", "HEALTHY", "CHANGES_PROPOSED", "CLEAN", "ISSUES_FOUND"}:
        return "✅"
    if value in {"INCOMPLETE", "NOT_WORKING", "BLOCKED_AUTH", "FAILED"}:
        return "⚠️"
    return "⏳"


def _home_text(app, state: dict) -> str:
    eng = (state or {}).get("engineering") or {}
    strategy = (state or {}).get("strategy") or {}
    cfg = _control.load(app)
    lines = [
        "<b>🤖 AI REPORTS &amp; OPERATIONS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Preferred masters — Strategy: <b>{html.escape(_master_label(cfg, 'strategy'))}</b> | "
        f"Engineering: <b>{html.escape(_master_label(cfg, 'engineering'))}</b>",
        "",
        "<b>ENGINEERING</b>",
    ]
    if eng.get("available"):
        lines += [
            f"GPT {_mark(eng.get('gpt'))}  Gemini {_mark(eng.get('gemini'))}  Copilot {_mark(eng.get('copilot'))}  Claude {_mark(eng.get('claude'))}",
            f"Master decision: <b>{html.escape(str(eng.get('master_status') or eng.get('master_engine') or 'WAITING'))}</b>",
        ]
    else:
        lines.append("⏳ No completed/published engineering cycle yet.")

    lines += ["", "<b>STRATEGY</b>"]
    if strategy.get("available"):
        lines += [
            f"GPT {_mark(strategy.get('gpt'))}  Gemini {_mark(strategy.get('gemini'))}  Copilot {_mark(strategy.get('copilot'))}  Claude {_mark(strategy.get('claude'))}",
            f"Cycle: <code>{html.escape(str(strategy.get('cycle_id') or '')[:80])}</code>",
            f"Master decision: <b>{'AVAILABLE' if strategy.get('master_decision_available') else 'WAITING'}</b>",
        ]
    else:
        lines.append("⏳ Waiting for the first published multi-agent strategy cycle.")

    if not state.get("fetch_ok", True):
        lines += ["", "⚠️ Latest ai-reviews fetch failed; showing cached state."]
    lines += ["", "Use <b>AI Master Control</b> to choose the preferred master and run Strategy/Engineering cycles."]
    return "\n".join(lines)


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _render(app, tid, text: str, kb: dict, cb=None):
    _sibot_ui._render(app, tid, text, kb, cb)


def _render_control(app, tid, cb=None):
    value = _control.load(app)
    _render(app, tid, _control_text(app), _control_keyboard(value), cb)


def _render_vps(app, tid, cb=None):
    _render(app, tid, _vps_text(app), _vps_keyboard(), cb)


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "menu:aiops" or data.startswith("aiops:") or data.startswith("aicfg:") or data.startswith("aivps:"):
            if not _is_master(app, tid):
                _answer(app, cb, "MASTER only")
                return
            if data == "aicfg:none":
                _answer(app, cb)
                return
            if data.startswith("aicfg:master:"):
                _, _, lane, provider = data.split(":", 3)
                _control.set_master(app, lane, provider, updated_by=tid)
                _answer(app, cb, f"{lane.title()} master: {_PROVIDER_LABELS.get(provider, provider)}")
                _render_control(app, tid, cb)
                return
            if data.startswith("aicfg:cycle:"):
                _, _, lane, mode = data.split(":", 3)
                _control.set_cycle(app, lane, mode, updated_by=tid)
                _answer(app, cb, f"{lane.title()} cycle: {mode}")
                _render_control(app, tid, cb)
                return
            if data.startswith("aicfg:enabled:"):
                _, _, lane, state = data.split(":", 3)
                _control.set_enabled(app, lane, state == "on", updated_by=tid)
                _answer(app, cb, f"{lane.title()} {'enabled' if state == 'on' else 'disabled'}")
                _render_control(app, tid, cb)
                return
            if data.startswith("aicfg:run:"):
                lane = data.rsplit(":", 1)[1]
                if lane == "both":
                    _control.request_run(app, "strategy", updated_by=tid)
                    _control.request_run(app, "engineering", updated_by=tid)
                    text = "Strategy + Engineering run requested"
                else:
                    _control.request_run(app, lane, updated_by=tid)
                    text = f"{lane.title()} run requested"
                _answer(app, cb, text)
                _render_control(app, tid, cb)
                return
            if data == "aivps:confirm:deploy":
                _answer(app, cb, "Confirm current-main deploy")
                _render(app, tid, "<b>⚠️ CONFIRM VPS DEPLOY</b>\n\nThis will deploy only the current GitHub <code>main</code> SHA through the restricted root wrapper. Full VPS tests must pass before the service restarts.", _vps_confirm_keyboard(), cb)
                return
            if data.startswith("aivps:run:"):
                action = data.rsplit(":", 1)[1]
                _control.request_vps_action(app, action, updated_by=tid)
                _answer(app, cb, f"Claude VPS {action} requested")
                _render_vps(app, tid, cb)
                return

            _answer(app, cb)
            state = snapshot_for_display(_ai._repo_root())
            if data == "aiops:audit":
                body = _ai._engineering_text(state)
            elif data == "aiops:decision":
                body = _ai._decision_text(state)
            elif data == "aiops:strategy":
                body = _ai._strategy_text(state)
            elif data == "aiops:updates":
                body = _ai._combined_text(state)
            elif data == "aiops:control":
                _render_control(app, tid, cb)
                return
            elif data == "aiops:vps":
                _render_vps(app, tid, cb)
                return
            else:
                body = _home_text(app, state)
            _render(app, tid, body, _ai_keyboard(), cb)
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_telegram_ai_reports_menu_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_ai_reports_menu_patch_installed = True


install()
