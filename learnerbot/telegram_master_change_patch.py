from __future__ import annotations

import asyncio
import html
import threading

from scripts import claude_division as _claude
from scripts import strategy_factory_transport as _sf

from . import ai_cost_router as _cost
from . import ai_ops_status as _status
from . import master_change_council as _change
from . import master_change_cost_router_patch as _cost_patch  # noqa: F401
from . import strategy_factory_council_transport_patch as _transport_patch  # noqa: F401
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


def _cost_status() -> str:
    body = _cost.format_snapshot()
    policy = (
        "\n\n<b>Routing policy</b>\n"
        "L0 mechanical → deterministic executor, 0 AI calls\n"
        "L1 routine → DeepSeek + GPT final for repo changes\n"
        "L2 normal engineering → DeepSeek + Gemini + GPT\n"
        "L3 important architecture → Gemini + Claude General + GPT\n"
        "L4 trading/security/deployment → full advisory council + GPT; protected action gates still apply"
    )
    return "<b>💰 AI Cost Router</b>\n\n" + _safe(body, 2500) + policy


def _chat_usage() -> str:
    agents = " | ".join(_sf.PUBLIC_TARGETS)
    return (
        "<b>🧠 Strategy Factory Chat</b>\n\n"
        "Choose the exact recipient. Claude has two explicit divisions.\n"
        f"Targets: <code>{_safe(agents,500)}</code>\n\n"
        "General discussion: <code>/aichat claude-general [risk model] challenge this design</code>\n"
        "Persistent coding: <code>/aichat claude-coding [websocket] inspect and fix this bug</code>\n"
        "Other agent: <code>/aichat gemini [RPC research] compare current options</code>\n\n"
        "Bare <code>claude</code> is intentionally rejected. Claude Coding is queued to its persistent Git mailbox and never silently falls back to Claude General."
    )


def _parse_chat_body(value: str) -> tuple[str, str]:
    body = str(value or "").strip()
    if not body.startswith("["):
        return "", body
    end = body.find("]")
    if end <= 1:
        return "", body
    subject = " ".join(body[1:end].split())
    message = body[end + 1 :].strip()
    if not subject or not message:
        return "", body
    return subject, message


def _chat_result_text(agent: str, result: dict) -> str:
    status = str(result.get("status") or "UNKNOWN").upper()
    acknowledged = bool(result.get("acknowledged"))
    reply = str(result.get("body") or "").strip()
    error = str(result.get("error") or "").strip()
    subject = str(result.get("subject") or "").strip()
    thread_id = str(result.get("thread_id") or "").strip()
    label = "Claude General" if agent == "claude-general" else agent.title()
    lines = [
        f"<b>🤖 {_safe(label,80)} — Strategy Factory</b>",
        f"Delivery: <b>{'ACKNOWLEDGED' if acknowledged else status}</b>",
    ]
    if agent == "claude-general":
        lines.append("Division: <b>GENERAL / AUTOMATED_GENERAL</b>")
    if subject:
        lines.append(f"Subject: <b>{_safe(subject,160)}</b>")
    if thread_id:
        lines.append(f"Thread: <code>{_safe(thread_id,140)}</code>")
    if reply:
        lines.extend(["", _safe(reply, 3500)])
    elif error:
        lines.extend(["", f"⚠️ {_safe(error,700)}"])
    else:
        lines.extend(["", f"⚠️ No agent reply. Final state: {_safe(status,80)}"])
    return "\n".join(lines)


def _master_chat_worker(app, tid, agent: str, body: str, subject: str = "") -> None:
    try:
        result = asyncio.run(_sf.exchange("master", agent, body, subject=subject, timeout=180.0))
        _ui._send(app, tid, _chat_result_text(agent, result))
    except Exception as exc:
        _ui._send(app, tid, f"⚠️ Strategy Factory chat failed: {_safe(exc,700)}")


def _start_master_chat(app, tid, agent: str, body: str, subject: str = "") -> None:
    thread = threading.Thread(
        target=_master_chat_worker,
        args=(app, tid, agent, body, subject),
        name=f"strategy-factory-chat-{agent}",
        daemon=True,
    )
    thread.start()


def _queue_claude_coding(app, tid, body: str, subject: str = "") -> None:
    try:
        thread_id, subject = _sf.resolve_thread(subject=subject)
        row = _claude.publish_coding_request(
            body,
            requested_by="MASTER",
            subject=subject,
            thread_id=thread_id,
        )
        lines = [
            "<b>🛠 CLAUDE CODING REQUEST QUEUED</b>",
            f"Reference: <code>{_safe(row.get('message_id'),160)}</code>",
            "Division: <b>CODING</b>",
            "Required identity: <b>PERSISTENT_AGENT</b>",
            "Transport: <b>Git mailbox</b>",
        ]
        if subject:
            lines.append(f"Subject: <b>{_safe(subject,160)}</b>")
        lines += [
            "",
            "<i>Queued does not prove the persistent Claude Coding session has read the request. There is no fallback to Claude General.</i>",
            f"Check a returned reply with <code>/aicoding {_safe(row.get('message_id'),160)}</code>.",
        ]
        _ui._send(app, tid, "\n".join(lines))
    except Exception as exc:
        _ui._send(app, tid, f"⚠️ Claude Coding routing failed: {_safe(exc,700)}")


def _coding_check_text(message_id: str) -> str:
    try:
        verified, raw = _claude.fetch_verified_coding_reply(expected_message_id=message_id)
    except Exception as exc:
        return (
            "<b>🛠 CLAUDE CODING REPLY</b>\n\n"
            f"Reference: <code>{_safe(message_id,160)}</code>\n"
            f"Status: <b>UNVERIFIED / NOT MATCHED</b>\n"
            f"{_safe(exc,700)}\n\n"
            "<i>A General/stateless reply or a reply missing CODING + PERSISTENT_AGENT cannot satisfy a Coding request.</i>"
        )
    payload = str(raw or "")
    body = payload.split("\n\n", 1)[1].strip() if "\n\n" in payload else payload
    return (
        "<b>🛠 CLAUDE CODING REPLY VERIFIED</b>\n"
        f"Reference: <code>{_safe(message_id,160)}</code>\n"
        f"Division: <b>{_safe(verified.get('division'),40)}</b>\n"
        f"Identity: <b>{_safe(verified.get('identity'),80)}</b>\n"
        f"Verification: <b>{_safe(verified.get('verification'),100)}</b>\n\n"
        f"{_safe(body,3500)}\n\n"
        "<i>Header verification is fail-closed but is not cryptographic session attestation.</i>"
    )


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

        if cmd == "/aicost":
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return
            _ui._send(app, tid, _cost_status())
            return

        if cmd == "/aicoding":
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return
            if not arg:
                _ui._send(app, tid, "Use <code>/aicoding MESSAGE_ID</code> to verify a Claude Coding reply.")
                return
            _ui._send(app, tid, _coding_check_text(arg.split()[0]))
            return

        if cmd == "/aichat":
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return
            chat_parts = arg.split(maxsplit=1) if arg else []
            if len(chat_parts) != 2:
                _ui._send(app, tid, _chat_usage())
                return
            agent = chat_parts[0].strip().lower()
            raw_body = chat_parts[1].strip()
            subject, body = _parse_chat_body(raw_body)
            if agent == "claude":
                _ui._send(app, tid, "⚠️ Claude division required. Use <code>claude-general</code> or <code>claude-coding</code>.")
                return
            if agent not in _sf.PUBLIC_TARGETS or not body:
                _ui._send(app, tid, _chat_usage())
                return
            if agent == "claude-coding":
                _queue_claude_coding(app, tid, body, subject)
                return
            subject_line = f" Subject: <b>{_safe(subject,160)}</b>." if subject else ""
            _ui._send(
                app,
                tid,
                f"📨 Sent to <b>{_safe(('Claude General' if agent == 'claude-general' else agent.title()),80)}</b>.{subject_line} Waiting for the correlated reply…",
            )
            _start_master_chat(app, tid, agent, body, subject)
            return

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
                    "\n\n<b>Flow</b>: one Strategy Factory WebSocket transport for General advisers; DIRECT handles one-to-one messages, "
                    "COUNCIL uses the same transport for cost-routed advisers → GPT final decision → deterministic policy gate → bounded implementation/tests. "
                    "Claude Coding remains a separate persistent repository identity. Critical trading/security/deployment requests still use protected gates."
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
                _ui._send(app, tid, f"🔄 Retry queued: <code>{_safe(state.get('request_id'),120)}</code>\nSuccessful adviser replies will be reused, not repurchased.")
                return

            try:
                state = _change.submit(app, tid, arg)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,700)}")
                return
            protected = bool(state.get("protected_reasons"))
            hard = bool(state.get("hard_protected_reasons"))
            route = state.get("cost_route") or {}
            advisers = ", ".join(route.get("advisers") or []) or "none"
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
                f"Transport: <b>Strategy Factory WebSocket</b> / COUNCIL mode\n"
                f"Cost route: <b>L{_safe(route.get('level'),10)}</b> — {_safe(route.get('reason'),500)}\n"
                f"Advisers: {_safe(advisers,300)} → GPT final\n"
                f"Planned model calls: {_safe(route.get('model_calls_before_implementation'),20)}"
                + note,
            )
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_master_change_patch_installed", False):
        return
    additions = (
        ("aichange", "MASTER cost-routed AI change review and GPT implementation"),
        ("aicost", "MASTER AI spend, budgets and routing status"),
        ("aichat", "MASTER chat with an explicit Strategy Factory/Claude division"),
        ("aicoding", "MASTER verify a Claude Coding reply by reference"),
    )
    for command in additions:
        if not any(cmd == command[0] for cmd, _ in _tgops.AI_MASTER_COMMANDS):
            _tgops.AI_MASTER_COMMANDS = tuple(_tgops.AI_MASTER_COMMANDS) + (command,)
    _tgops.snapshot_for_display = snapshot_with_master_change
    _tgops.transition_messages = transitions_with_master_change
    _ui.handle_update = handle_update
    _ui._telegram_master_change_patch_installed = True


install()
