from __future__ import annotations

import threading
import time
from pathlib import Path

from . import ai_health_compact_report_patch as _compact
from . import telegram as _tg
from . import telegram_ai_health_truth_patch as _truth
from . import cli as _cli
from .ai_ops_status import master_chat_ids

_PREV_APP = _cli._app
_SEND_MARKER = ".ai_health_mobile_layout_v2_sent"
_SEND_LOCK = threading.Lock()
_SEND_STARTED = False


def _short_status(status: str) -> tuple[str, str]:
    """Turn diagnostic health wording into mobile-width primary/secondary lines."""
    text = str(status or "").strip()

    if text == "Worker connected":
        return "Connected", ""
    if text == "Worker disconnected":
        return "Disconnected", ""
    if text == "Worker connected · API working":
        return "Connected", "API OK now"
    if text == "Worker connected · API/provider problem":
        return "Connected", "API problem"
    if text.startswith("Worker connected · API last OK ") and text.endswith(" ago"):
        age = text[len("Worker connected · API last OK ") : -len(" ago")]
        return "Connected", f"API OK · {age} ago"
    if text.startswith("Worker connected · API last problem ") and text.endswith(" ago"):
        age = text[len("Worker connected · API last problem ") : -len(" ago")]
        return "Connected", f"API problem · {age} ago"

    if text == "API working":
        return "API OK", ""
    if text == "API/provider problem":
        return "API problem", ""
    if text == "API status unavailable":
        return "Needs verification", "API status unavailable"
    if text.startswith("API not checked for "):
        detail = text[len("API not checked for ") :]
        age, _, last = detail.partition(" · ")
        return "Needs verification", f"{last or 'API stale'} · {age} ago"
    if text.startswith("API unverified for "):
        detail = text[len("API unverified for ") :]
        age, _, last = detail.partition(" · ")
        return "API unverified", f"{last or 'No fresh check'} · {age}"

    replacements = {
        "Agent working": "Working",
        "Agent partly verified": "Partly verified",
        "Agent state mixed": "Mixed state",
        "Agent problem": "Agent problem",
        "Agent state pending": "Pending",
        "Assigned": "Connected",
        "Assignment pending": "Assignment pending",
        "Assignment/auth problem": "Assignment/auth problem",
    }
    return replacements.get(text, text or "Unknown"), ""


def provider_health_text(engineering: dict, strategy: dict) -> str:
    """Mobile-width agent health: short primary line plus optional API sub-line."""
    preflight = _truth._fresh_preflight()
    runtime = _truth._runtime_connections()
    rows: list[tuple[str, str, str, str]] = []
    for provider in _compact.PROVIDERS:
        icon, status = _truth._provider_status(provider, engineering, strategy, preflight, runtime)
        primary, secondary = _short_status(status)
        rows.append((provider, icon, primary, secondary))

    healthy = sum(1 for _provider, icon, _primary, _secondary in rows if icon == "🟢")
    verify = sum(1 for _provider, icon, _primary, _secondary in rows if icon == "🟡")
    issues = len(rows) - healthy - verify
    overall = "🔴" if issues else ("🟡" if verify else "🟢")

    lines = [
        _compact._AI_HEALTH_HEADING,
        f"{overall} <b>{healthy} healthy</b> | {verify} verify | {issues} issues",
        "",
    ]
    for provider, icon, primary, secondary in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {primary}")
        if secondary:
            lines.append(f"↳ <i>{secondary}</i>")
    return "\n".join(lines)


def lane_summary_text(lane: str, health: dict) -> str:
    heading = _compact._ENGINEERING_HEADING if lane == "engineering" else _compact._STRATEGY_HEADING
    stale = _truth._review_stale_reason(lane, health)
    if stale:
        return "\n".join([
            heading,
            "",
            "🟡 <b>Snapshot stale</b>",
            "↳ Refresh needed",
        ])

    rows = _truth._classified_rows(lane, health)
    overall, _summary = _truth._summary_for_rows(rows)
    working = sum(1 for _provider, icon, _status in rows if icon == "🟢")
    pending = sum(1 for _provider, icon, _status in rows if icon == "🟡")
    issues = len(rows) - working - pending
    lines = [
        heading,
        "",
        f"{overall} <b>{working} working</b> | {pending} pending | {issues} issues",
    ]
    for provider, icon, status in rows:
        if icon in {"🔴", "🟠"}:
            lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def factory_summary_text(health: dict) -> str:
    agents = (health or {}).get("agents") or {}
    idle = bool(agents) and all(
        str((agents.get(provider) or {}).get("state") or "WAITING").upper() == "WAITING"
        and any(
            marker in str((agents.get(provider) or {}).get("reason") or "").lower()
            for marker in ("no strategy room request", "stale")
        )
        for provider in _compact.PROVIDERS
    )
    if idle:
        return "\n".join([
            _compact._STRATEGY_FACTORY_HEADING,
            "",
            "⚪ <b>Idle</b>",
            "↳ No active request",
        ])

    rows = _truth._classified_rows("strategy_room", health)
    overall, _summary = _truth._summary_for_rows(rows)
    working = sum(1 for _provider, icon, _status in rows if icon == "🟢")
    pending = sum(1 for _provider, icon, _status in rows if icon == "🟡")
    issues = len(rows) - working - pending
    lines = [
        _compact._STRATEGY_FACTORY_HEADING,
        "",
        f"{overall} <b>{working} working</b> | {pending} pending | {issues} issues",
    ]
    for provider, icon, status in rows:
        if icon in {"🔴", "🟠"}:
            lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def lane_text(lane: str, health: dict | None = None) -> str:
    """Dedicated drill-down with spacing; provider/API health stays out of this lane."""
    health = health if health is not None else _compact._lane_health(lane)
    heading = _compact._ENGINEERING_HEADING if lane == "engineering" else _compact._STRATEGY_HEADING
    rows = _truth._classified_rows(lane, health)
    lines = [heading, ""]
    stale = _truth._review_stale_reason(lane, health)
    if stale:
        lines += ["🟡 <b>Snapshot stale</b>", "↳ Historical detail below", ""]
    for provider, icon, status in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def strategy_room_text(health: dict | None = None) -> str:
    health = health if health is not None else _compact._strategy_room_health()
    rows = _truth._classified_rows("strategy_room", health)
    lines = [_compact._STRATEGY_FACTORY_HEADING, ""]
    for provider, icon, status in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def dashboard_text(
    engineering: dict | None = None,
    strategy: dict | None = None,
    strategy_room: dict | None = None,
) -> str:
    engineering = engineering if engineering is not None else _compact._lane_health("engineering")
    strategy = strategy if strategy is not None else _compact._lane_health("strategy")
    strategy_room = strategy_room if strategy_room is not None else _compact._strategy_room_health()
    return "\n\n".join([
        provider_health_text(engineering, strategy),
        lane_summary_text("engineering", engineering),
        lane_summary_text("strategy", strategy),
        factory_summary_text(strategy_room),
    ])


def _send_mobile_dashboard_once(app) -> None:
    marker = Path(app.data_dir) / _SEND_MARKER
    if marker.exists() or not getattr(app, "telegram_bot_token", ""):
        return

    # Wait until the broker has registered the production workers so the message
    # cannot regress to stale review/API fallbacks just because startup is still racing.
    deadline = time.time() + 10
    while time.time() < deadline:
        runtime = _truth._runtime_connections()
        connected = runtime.get("connected_agents") or set()
        if runtime.get("available") and len(connected) >= len(_compact.PROVIDERS):
            break
        time.sleep(0.5)

    chats = master_chat_ids(app.csv_dir)
    if not chats:
        print("[ai-health-mobile] no MASTER chat available; update not sent")
        return

    text = dashboard_text()
    _tg.send_to_chats(
        app.telegram_bot_token,
        chats,
        text,
        disable_notification=False,
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(int(time.time())), encoding="utf-8")
    print(f"[ai-health-mobile] sent updated dashboard to {len(chats)} MASTER chat(s)")


def _start_one_shot_sender(app) -> None:
    global _SEND_STARTED
    if not _truth._is_production_runtime_process():
        return
    with _SEND_LOCK:
        if _SEND_STARTED:
            return
        _SEND_STARTED = True
    threading.Thread(
        target=_send_mobile_dashboard_once,
        args=(app,),
        name="ai-health-mobile-one-shot",
        daemon=True,
    ).start()


def _app_with_mobile_dashboard_send():
    app = _PREV_APP()
    try:
        _start_one_shot_sender(app)
    except Exception as exc:
        print(f"[ai-health-mobile] sender start failed: {type(exc).__name__}: {exc}")
    return app


def install() -> None:
    if getattr(_compact, "_mobile_health_layout_installed", False):
        return
    _compact.dashboard_text = dashboard_text
    _compact._lane_text = lane_text
    _compact.strategy_room_text = strategy_room_text
    _cli._app = _app_with_mobile_dashboard_send
    _compact._mobile_health_layout_installed = True


install()
