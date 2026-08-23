from __future__ import annotations

import asyncio
import html
import threading

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
        "L3 important architecture → Gemini + Claude + GPT\n"
        "L4 trading/security/deployment → full council + GPT"
    )
    return "<b>💰 AI Cost Router</b>\n\n" + _safe(body, 2500) + policy


def _chat_usage() -> str:
    agents = " | ".join(_sf.AGENTS)
    return (
        "<b>🧠 Strategy Factory Chat</b>\n\n"
        "Use the persistent Strategy Factory identity instead of a separate vendor browser chat.\n"
        f"Agents: <code>{_safe(agents,300)}</code>\n\n"
        "Example: <code>/aichat gemini what did GPT ask you?</code>"
    )


def _chat_result_text(agent: str, result: dict) -> str:
    status = str(result.get("status") or "UNKNOWN").upper()
    acknowledged = bool(result.get("acknowledged"))
    reply = str(result.get("body") or "").strip()
    error = str(result.get("error") or "").strip()
    lines = [
        f"<b>🤖 {_safe(agent.title(),80)} — Strategy Factory</b>",
        f"Delivery: <b>{'ACKNOWLEDGED' if acknowledged else status}</b>",
    ]
    if reply:
        lines.extend(["", _safe(reply, 3500)])
    elif error:
        lines.extend(["", f"⚠️ {_safe(error,700)}"])
    else:
        lines.extend(["", f"⚠️ No agent reply. Final state: {_safe(status,80)}"])
    return "\n".join(lines)


def _master_chat_worker(app, tid, agent: str, body: str) -> None:
    try:
        result = asyncio.run(_sf.exchange("master", agent, body, timeout=180.0))
        _ui._send(app, tid, _chat_result_text(agent, result))
    except Exception as exc:
        _ui._send(app, tid, f"⚠️ Strategy Factory chat failed: {_safe(exc,700)}")


def _start_master_chat(app, tid, agent: str, body: str) -> None:
    thread = threading.Thread(
        target=_master_chat_worker,
        args=(app, tid, agent, body),
        name=f"strategy-factory-chat-{agent}",
        daemon=True,
    )
    thread.start()


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
            body = chat_parts[1].strip()
            if agent not in _sf.AGENTS or not body:
                _ui._send(app, tid, _chat_usage())
                return
            _ui._send(
                app,
                tid,
                f"📨 Sent to <b>{_safe(agent.title(),80)}</b> via the persistent Strategy Factory identity. Waiting for the correlated reply…",
            )
            _start_master_chat(app, tid, agent, body)
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
                    "\n\n<b>Flow</b>: one Strategy Factory WebSocket transport; DIRECT handles one-to-one messages, "
                    "COUNCIL uses the same transport for cost-routed advisers → GPT final decision → deterministic policy gate → GPT implementation/tests. "
                    "Critical trading/security/deployment requests still use the full council."
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
    if not any(cmd == "aichange" for cmd, _ in _tgops.AI_MASTER_COMMANDS):
        _tgops.AI_MASTER_COMMANDS = tuple(_tgops.AI_MASTER_COMMANDS) + (
            ("aichange", "MASTER cost-routed AI change review and GPT implementation"),
        )
    if not any(cmd == "aicost" for cmd, _ in _tgops.AI_MASTER_COMMANDS):
        _tgops.AI_MASTER_COMMANDS = tuple(_tgops.AI_MASTER_COMMANDS) + (
            ("aicost", "MASTER AI spend, budgets and routing status"),
        )
    if not any(cmd == "aichat" for cmd, _ in _tgops.AI_MASTER_COMMANDS):
        _tgops.AI_MASTER_COMMANDS = tuple(_tgops.AI_MASTER_COMMANDS) + (
            ("aichat", "MASTER persistent chat with a Strategy Factory agent"),
        )
    _tgops.snapshot_for_display = snapshot_with_master_change
    _tgops.transition_messages = transitions_with_master_change
    _ui.handle_update = handle_update
    _ui._telegram_master_change_patch_installed = True


install()
