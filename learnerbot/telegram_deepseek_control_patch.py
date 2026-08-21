from __future__ import annotations

import html
import time

from . import ai_master_control as _control
from . import telegram_ai_ops_patch as _ai
from . import telegram_ai_reports_menu_patch as _menu
from . import telegram_ui as _ui
from .ai_ops_status import fetch_ai_reviews, read_json

_PREV_HANDLE = _ui.handle_update
_PREV_AI_KEYBOARD = _menu._ai_keyboard
_PENDING_TASK: dict[str, dict] = {}
TASK_TTL_SECONDS = 600


def _deepseek_button_present(rows: list[list[dict]]) -> bool:
    return any(
        any(str(button.get("callback_data") or "") == "dsctl:menu" for button in row)
        for row in rows
    )


def ai_keyboard_with_deepseek() -> dict:
    kb = _PREV_AI_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    if not _deepseek_button_present(rows):
        insert_at = 2 if len(rows) >= 2 else len(rows)
        rows.insert(insert_at, [{"text": "🔴 DeepSeek GitHub & VPS", "callback_data": "dsctl:menu"}])
    return {"inline_keyboard": rows}


def _keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔴 DEEPSEEK GITHUB", "callback_data": "dsctl:none"}],
            [
                {"text": "🔎 Inspect repo", "callback_data": "dsctl:gh:inspect"},
                {"text": "🧪 Run repo tests", "callback_data": "dsctl:gh:test"},
            ],
            [{"text": "📝 Inspect specific task", "callback_data": "dsctl:gh:ask"}],
            [{"text": "🛠 Draft fix from task", "callback_data": "dsctl:gh:fix"}],
            [{"text": "🖥 DEEPSEEK VPS", "callback_data": "dsctl:none"}],
            [
                {"text": "🔎 Inspect server", "callback_data": "dsctl:vps:inspect"},
                {"text": "🧪 Test server", "callback_data": "dsctl:vps:test"},
            ],
            [{"text": "🚀 Deploy CURRENT main", "callback_data": "dsctl:vps:confirm"}],
            [
                {"text": "🔄 Refresh", "callback_data": "dsctl:menu"},
                {"text": "⬅️ AI Reports", "callback_data": "menu:aiops"},
            ],
        ]
    }


def _confirm_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Confirm DeepSeek deploy CURRENT main", "callback_data": "dsctl:vps:deploy"}],
            [{"text": "❌ Cancel", "callback_data": "dsctl:menu"}],
        ]
    }


def _latest_results() -> tuple[dict, dict]:
    root = _ai._repo_root()
    try:
        fetch_ai_reviews(root, timeout=12)
    except Exception:
        pass
    github = read_json(root, "github/deepseek/latest.json") or {}
    vps = read_json(root, "vps/deepseek/latest.json") or {}
    return (github if isinstance(github, dict) else {}, vps if isinstance(vps, dict) else {})


def _status_line(label: str, result: dict) -> str:
    if not result:
        return f"{label}: <b>no completed result yet</b>"
    status = str(result.get("status") or "UNKNOWN").upper()
    action = str(result.get("action") or "").upper()
    icon = "✅" if status == "SUCCESS" else ("⚠️" if status in {"FAILED", "ERROR"} else "⏳")
    return f"{label}: {icon} <b>{html.escape(status)}</b> {html.escape(action)}"


def _text(app) -> str:
    cfg = _control.load(app)
    github, vps = _latest_results()
    task = str(cfg.get("deepseek_github_task") or "").strip()
    if len(task) > 180:
        task = task[:180] + "…"
    lines = [
        "<b>🔴 DEEPSEEK CONTROL</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "Use Telegram as the bridge to DeepSeek's bounded GitHub Actions. The trading process itself receives no GitHub credential.",
        "",
        "<b>GitHub</b>",
        f"Requested: <b>{html.escape(str(cfg.get('deepseek_github_action') or 'none').upper())}</b> #{int(cfg.get('deepseek_github_action_nonce') or 0)}",
    ]
    if task:
        lines.append(f"Task: <code>{html.escape(task)}</code>")
    lines.append(_status_line("Last completed GitHub result", github))
    if github.get("pr_url"):
        lines.append(f"Draft PR: {html.escape(str(github.get('pr_url'))[:300])}")
    summary = str(github.get("summary") or "").strip()
    if summary:
        lines += ["", "<b>DeepSeek GitHub result:</b>", html.escape(summary[:900])]

    lines += [
        "",
        "<b>VPS</b>",
        f"Requested: <b>{html.escape(str(cfg.get('deepseek_vps_action') or 'none').upper())}</b> #{int(cfg.get('deepseek_vps_action_nonce') or 0)}",
        _status_line("Last completed VPS result", vps),
    ]
    analysis = str(vps.get("deepseek_analysis") or "").strip()
    if analysis:
        lines += ["", "<b>DeepSeek VPS analysis:</b>", html.escape(analysis[:900])]
    lines += [
        "",
        "⏱ Telegram requests are picked up by the sanitised GitHub bridge within about 5 minutes.",
        "🔒 No unrestricted root/sudo, wallet/private-key access, arbitrary deploy SHA, self-merge, or LIVE/risk-gate bypass.",
    ]
    return "\n".join(lines)


def _render(app, tid, cb=None) -> None:
    _menu._render(app, tid, _text(app), _keyboard(), cb)


def _prompt_task(app, tid, action: str) -> None:
    _PENDING_TASK[str(tid)] = {
        "action": action,
        "expires": time.time() + TASK_TTL_SECONDS,
    }
    title = "Inspect specific repository task" if action == "inspect" else "Create bounded draft fix"
    _ui._send(
        app,
        tid,
        "\n".join([
            f"<b>🔴 {title}</b>",
            "",
            "Send the task for DeepSeek in your next Telegram message.",
            "Example: <code>Check why Solana entries are being rejected and report the proven cause.</code>",
            "",
            "For draft fixes, DeepSeek can only create a tested <b>draft PR</b>; it cannot merge or deploy it.",
            "Send <code>cancel</code> to stop.",
        ]),
    )


def _handle_pending(app, message: dict) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None:
        return False
    pending = _PENDING_TASK.get(str(tid))
    if not pending:
        return False
    if not _menu._is_master(app, tid):
        _PENDING_TASK.pop(str(tid), None)
        return False
    text = str(message.get("text") or "").strip()
    if text.startswith("/"):
        return False
    if time.time() > float(pending.get("expires") or 0):
        _PENDING_TASK.pop(str(tid), None)
        _ui._send(app, tid, "⌛ DeepSeek task request expired. Open DeepSeek Control and try again.")
        return True
    if text.lower() in {"cancel", "cancel."}:
        _PENDING_TASK.pop(str(tid), None)
        _ui._send(app, tid, "✅ DeepSeek task cancelled.", _keyboard())
        return True
    if not text:
        _ui._send(app, tid, "❌ Send a non-empty task or <code>cancel</code>.")
        return True
    action = str(pending.get("action") or "inspect")
    try:
        state = _control.request_deepseek_github_action(app, action, task=text, updated_by=tid)
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}")
        return True
    _PENDING_TASK.pop(str(tid), None)
    _ui._send(
        app,
        tid,
        f"✅ 🔴 DeepSeek GitHub <b>{html.escape(action.upper())}</b> queued as request #{int(state.get('deepseek_github_action_nonce') or 0)}.\nThe GitHub bridge will dispatch it within about 5 minutes.",
        _keyboard(),
    )
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    if message and _handle_pending(app, message):
        return

    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data.startswith("dsctl:"):
            if not _menu._is_master(app, tid):
                _menu._answer(app, cb, "MASTER only")
                return
            if data == "dsctl:none":
                _menu._answer(app, cb)
                return
            if data == "dsctl:menu":
                _menu._answer(app, cb)
                _render(app, tid, cb)
                return
            if data == "dsctl:gh:inspect":
                state = _control.request_deepseek_github_action(app, "inspect", updated_by=tid)
                _menu._answer(app, cb, f"DeepSeek repo inspect queued #{state['deepseek_github_action_nonce']}")
                _render(app, tid, cb)
                return
            if data == "dsctl:gh:test":
                state = _control.request_deepseek_github_action(app, "test", updated_by=tid)
                _menu._answer(app, cb, f"Repository tests queued #{state['deepseek_github_action_nonce']}")
                _render(app, tid, cb)
                return
            if data == "dsctl:gh:ask":
                _menu._answer(app, cb, "Send DeepSeek's inspection task")
                _prompt_task(app, tid, "inspect")
                return
            if data == "dsctl:gh:fix":
                _menu._answer(app, cb, "Send the bounded draft-fix task")
                _prompt_task(app, tid, "draft_fix")
                return
            if data == "dsctl:vps:inspect":
                state = _control.request_deepseek_vps_action(app, "inspect", updated_by=tid)
                _menu._answer(app, cb, f"DeepSeek VPS inspect queued #{state['deepseek_vps_action_nonce']}")
                _render(app, tid, cb)
                return
            if data == "dsctl:vps:test":
                state = _control.request_deepseek_vps_action(app, "test", updated_by=tid)
                _menu._answer(app, cb, f"DeepSeek VPS tests queued #{state['deepseek_vps_action_nonce']}")
                _render(app, tid, cb)
                return
            if data == "dsctl:vps:confirm":
                _menu._answer(app, cb, "Confirmation required")
                _menu._render(
                    app,
                    tid,
                    "<b>⚠️ CONFIRM DEEPSEEK VPS DEPLOY</b>\n\nDeepSeek may deploy only the exact current GitHub <code>main</code> SHA through the restricted deployment wrapper. The wrapper still runs its server-side safety/tests.\n\nNo arbitrary SHA or unrestricted sudo is allowed.",
                    _confirm_keyboard(),
                    cb,
                )
                return
            if data == "dsctl:vps:deploy":
                state = _control.request_deepseek_vps_action(app, "deploy", updated_by=tid)
                _menu._answer(app, cb, f"Current-main deploy queued #{state['deepseek_vps_action_nonce']}")
                _render(app, tid, cb)
                return

    return _PREV_HANDLE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_deepseek_control_patch_installed", False):
        return
    _menu._ai_keyboard = ai_keyboard_with_deepseek
    _ui.handle_update = handle_update
    _ui._telegram_deepseek_control_patch_installed = True


install()
