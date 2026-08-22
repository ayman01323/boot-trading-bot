from __future__ import annotations

import html

from . import ai_ops_status as _status
from . import master_change_council as _change
from . import telegram_ai_ops_patch as _tgops
from . import telegram_ui as _ui

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SNAPSHOT = _tgops.snapshot_for_display
_PREV_TRANSITIONS = _tgops.transition_messages


def _safe(value, limit=1200):
    return html.escape(str(value or "")[:limit])


def _remote_result_text(result: dict | None) -> str:
    row = result or {}
    if not row:
        return ""
    lines = [
        "",
        "<b>Repository action</b>",
        f"State: <b>{_safe(row.get('state') or row.get('status') or 'UNKNOWN',120)}</b>",
    ]
    if row.get("pr_url"):
        lines.append(f"PR: {_safe(row.get('pr_url'),400)}")
    if row.get("implemented_sha"):
        lines.append(f"SHA: <code>{_safe(str(row.get('implemented_sha'))[:12],20)}</code>")
    if row.get("detail"):
        lines.append(_safe(row.get("detail"),700))
    return "\n".join(lines)


def _local_status(app) -> str:
    state = _change.latest(app)
    if not state:
        return (
            "<b>🧠 MASTER AI CHANGE COUNCIL</b>\n\n"
            "No request yet.\n\n"
            "Use: <code>/aichange describe the change you want</code>"
        )
    body = _change.status_text(state)
    return "<b>🧠 MASTER AI CHANGE COUNCIL</b>\n\n" + _safe(body, 3500).replace("\n", "\n")


def snapshot_with_master_change(repo_root):
    state = dict(_PREV_SNAPSHOT(repo_root) or {})
    state["master_change"] = _status.read_json(repo_root, "master-change/latest_result.json") or {}
    return state


def transitions_with_master_change(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    old = (previous or {}).get("master_change") or {}
    new = (current or {}).get("master_change") or {}
    if not new:
        return messages
    old_key = (str(old.get("request_id") or ""), str(old.get("state") or old.get("status") or ""), str(old.get("pr_url") or ""))
    new_key = (str(new.get("request_id") or ""), str(new.get("state") or new.get("status") or ""), str(new.get("pr_url") or ""))
    if new_key != old_key:
        text = (
            "🛠 GPT MASTER CHANGE UPDATE\n"
            f"Request: {new.get('request_id') or '-'}\n"
            f"State: {new.get('state') or new.get('status') or 'UNKNOWN'}"
        )
        if new.get("pr_url"):
            text += f"\nPR: {new.get('pr_url')}"
        if new.get("detail"):
            text += f"\n{str(new.get('detail'))[:700]}"
        messages.append(text)
    return messages


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/aichange":
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return

            if not arg or arg.lower() == "status":
                body = _local_status(app)
                try:
                    remote = snapshot_with_master_change(_tgops._repo_root()).get("master_change") or {}
                except Exception:
                    remote = {}
                if remote:
                    body += _remote_result_text(remote)
                body += (
                    "\n\n<b>Flow</b>: MASTER request → Claude + Gemini + DeepSeek + Copilot independent advice "
                    "→ GPT final decision → deterministic policy gate → GPT implementation/tests."
                )
                _ui._send(app, tid, body)
                return

            lower = arg.lower()
            if lower.startswith("retry "):
                request_id = arg.split(None, 1)[1].strip()
                try:
                    state = _change.retry(app, request_id, tid)
                except Exception as exc:
                    _ui._send(app, tid, f"⚠️ {_safe(exc,500)}")
                    return
                _ui._send(app, tid, f"🔄 Retry queued: <code>{_safe(state.get('request_id'),120)}</code>")
                return

            try:
                state = _change.submit(app, tid, arg)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,700)}")
                return
            protected = bool(state.get("protected_reasons"))
            hard = bool(state.get("hard_protected_reasons"))
            note = ""
            if hard:
                note = "\n⚠️ This request contains hard-protected subject matter; the council may advise, but implementation is fail-closed."
            elif protected:
                note = "\n🛡 Protected subject matter detected; automatic merge is disabled even if GPT approves a draft change."
            _ui._send(
                app,
                tid,
                "🧠 <b>AI CHANGE REQUEST ACCEPTED</b>\n"
                f"ID: <code>{_safe(state.get('request_id'),120)}</code>\n"
                "All four adviser agents will review independently. GPT then makes the final decision."
                + note,
            )
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_master_change_patch_installed", False):
        return
    if not any(cmd == "aichange" for cmd, _ in _tgops.AI_MASTER_COMMANDS):
        _tgops.AI_MASTER_COMMANDS = tuple(_tgops.AI_MASTER_COMMANDS) + (
            ("aichange", "MASTER request five-agent review and GPT change"),
        )
    _tgops.snapshot_for_display = snapshot_with_master_change
    _tgops.transition_messages = transitions_with_master_change
    _ui.handle_update = handle_update
    _ui._telegram_master_change_patch_installed = True


install()
