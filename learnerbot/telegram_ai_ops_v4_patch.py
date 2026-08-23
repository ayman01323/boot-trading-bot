from __future__ import annotations

import asyncio
import html
import queue
import threading
import time
from pathlib import Path

from scripts import strategy_factory_transport as _sf

from . import ai_ops_v4 as _v4
from . import cli as _cli
from . import hourly_capital_alert_patch as _loss
from . import telegram as _tg
from . import telegram_ai_ops_patch as _aiops
from . import telegram_profit_report_alerts_patch as _profit
from . import telegram_ui as _ui

_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_LOSS_ALERT = _loss.send_new_loss_alerts
_PREV_PROFIT_ALERT = _profit.send_new_profit_alerts
_PREV_SCHEDULED_REPORT_TEXT = _loss.scheduled_report_text
_PREV_TRANSITIONS = _aiops.transition_messages
_PREV_SEND_TO_CHATS = _tg.send_to_chats

_APP = None
_CASE_QUEUE: queue.Queue[dict] = queue.Queue()
_CASE_THREAD_STARTED = False
_CASE_LOCK = threading.Lock()

V4_COMMANDS = (
    ("aievents", "MASTER recent unified AI operations events"),
    ("aicases", "MASTER open Strategy/Engineering/Factory cases"),
    ("aiscores", "MASTER AI contribution score ledger"),
    ("aigaps", "MASTER blocked implementation gap/cost reports"),
    ("aiv4", "MASTER AI Operations Constitution V4 status"),
)


def _safe(value, limit=1200):
    return html.escape(str(value or "")[:limit])


def _position_meta(app, tid) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        for row in _loss._sibot.position_rows(app, tid, open_only=True):
            cid = int(row.get("chain_id") or 0)
            pid = str(row.get("position_id") or f"evm:{cid}:{row.get('token')}")
            out[pid] = dict(row)
    except Exception:
        pass
    try:
        for row in _loss._sol.position_rows(app, tid, open_only=True):
            pid = str(row.get("position_id") or f"sol:{row.get('mint')}")
            out[pid] = dict(row)
    except Exception:
        pass
    return out


def _case_file(app) -> Path:
    return _v4._ops_root(app) / "cases.json"


def _set_case_analysis(app, case_id: str, status: str, *, detail: str = "") -> None:
    with _CASE_LOCK:
        path = _case_file(app)
        rows = _v4._read_json(path, [])
        if not isinstance(rows, list):
            return
        for row in rows:
            if str((row or {}).get("case_id")) == str(case_id):
                row["factory_analysis_status"] = str(status)
                row["factory_analysis_updated_at"] = int(time.time())
                if detail:
                    row["factory_analysis_detail"] = str(detail)[:4000]
                break
        _v4._atomic_json(path, rows[-500:])


def _claim_case_analysis(app, case: dict | None) -> bool:
    if not case:
        return False
    case_id = str(case.get("case_id") or "")
    if not case_id:
        return False
    with _CASE_LOCK:
        path = _case_file(app)
        rows = _v4._read_json(path, [])
        if not isinstance(rows, list):
            return False
        now = int(time.time())
        for row in rows:
            if str((row or {}).get("case_id")) != case_id:
                continue
            status = str(row.get("factory_analysis_status") or "")
            updated = int(row.get("factory_analysis_updated_at") or 0)
            if status in {"QUEUED", "RUNNING", "DONE"} and now - updated < 86400:
                return False
            row["factory_analysis_status"] = "QUEUED"
            row["factory_analysis_updated_at"] = now
            _v4._atomic_json(path, rows[-500:])
            return True
    return False


def _queue_case(app, case: dict | None, event: dict | None) -> None:
    if not case or not event:
        return
    if str(event.get("severity") or "") not in {"P0", "P1"} and not (
        str(event.get("severity") or "") == "P2" and int(event.get("occurrence_count") or 1) >= 3
    ):
        return
    if _claim_case_analysis(app, case):
        _CASE_QUEUE.put({"app": app, "case": dict(case), "event": dict(event)})


def _factory_prompt(case: dict, event: dict) -> str:
    return (
        "You are GPT acting as Strategy Factory triage for a structured AI Operations incident.\n"
        "REPORT/RESEARCH ONLY. Do not trade, deploy, alter LIVE/ARMED/capital, wallets/signing, stop-loss, "
        "risk thresholds or circuit breakers. Do not treat this alert as proof of root cause.\n\n"
        f"Case ID: {case.get('case_id')}\n"
        f"Correlation: {case.get('correlation_id')}\n"
        f"Severity: {case.get('severity')}\n"
        f"Owner monitor: {case.get('owner_monitor')}\n"
        f"Event type: {event.get('event_type')}\n"
        f"Chain: {event.get('chain')}\n"
        f"Strategy: {event.get('strategy_id')} {event.get('strategy_version')}\n"
        f"Git SHA: {event.get('git_sha')}\n"
        f"Trade IDs: {event.get('trade_ids')}\n"
        f"Message: {event.get('message')}\n"
        f"Technical impact: {event.get('technical_impact')}\n"
        f"Financial impact: {event.get('financial_impact')}\n\n"
        "Return a concise evidence-led triage with: proven facts; competing hypotheses; what Engineering must check; "
        "what Strategy Monitor must check; one adversarial falsification test; recommended Factory action from "
        "REPORT/RESEARCH/SHADOW_PROPOSE/CODE_DRAFT only; missing data/tools/cost if blocked. Never recommend an "
        "automatic LIVE promotion or protected state change."
    )


def _case_worker() -> None:
    while True:
        item = _CASE_QUEUE.get()
        app = item.get("app")
        case = item.get("case") or {}
        event = item.get("event") or {}
        case_id = str(case.get("case_id") or "")
        try:
            _set_case_analysis(app, case_id, "RUNNING")
            result = asyncio.run(_sf.exchange(
                "master",
                "gpt",
                _factory_prompt(case, event),
                subject=f"AI Ops incident {case_id}",
                timeout=180.0,
            ))
            status = str(result.get("status") or "").upper()
            reply = str(result.get("body") or "").strip()
            if status != "REPLIED" or not reply:
                raise RuntimeError(str(result.get("error") or f"GPT triage ended {status}"))
            _v4.record_event(
                app,
                event_type="FACTORY_REPORT",
                source_component="strategy_factory_gpt_triage",
                message=reply,
                severity="P2",
                chain=str(event.get("chain") or ""),
                strategy_id=str(event.get("strategy_id") or ""),
                strategy_version=str(event.get("strategy_version") or ""),
                git_sha=str(event.get("git_sha") or ""),
                trade_ids=list(event.get("trade_ids") or []),
                evidence_refs=[case_id, str(event.get("event_id") or "")],
                correlation_id=str(case.get("correlation_id") or ""),
                owner_monitor="FACTORY",
                allowed_actions=["REPORT", "RESEARCH", "SHADOW_PROPOSE"],
            )
            _set_case_analysis(app, case_id, "DONE", detail=reply)
        except Exception as exc:
            _set_case_analysis(app, case_id, "FAILED", detail=f"{type(exc).__name__}: {exc}")
            print(f"[ai-ops-v4-case:{case_id}] {type(exc).__name__}: {exc}")
        finally:
            _CASE_QUEUE.task_done()


def _start_case_worker() -> None:
    global _CASE_THREAD_STARTED
    with _CASE_LOCK:
        if _CASE_THREAD_STARTED:
            return
        threading.Thread(target=_case_worker, name="ai-ops-v4-factory-cases", daemon=True).start()
        _CASE_THREAD_STARTED = True
        print("[ai-ops-v4] Strategy Factory incident triage worker started")


def _loss_event(app, tid, count: int) -> None:
    if count <= 0:
        return
    threshold = _loss.loss_alert_threshold_pct(app, tid)
    rows = list(_loss._live_loss_rows(app, tid, threshold))
    meta = _position_meta(app, tid)
    affected = rows[:10]
    chains = sorted({str(r.get("chain") or "") for r in affected if r.get("chain")})
    pids = []
    versions = set()
    shas = set()
    engines = set()
    for row in affected:
        key = row.get("key") or ()
        pid = str(key[2]) if len(key) > 2 else ""
        if pid:
            pids.append(pid)
        raw = meta.get(pid) or {}
        if raw.get("strategy_version"):
            versions.add(str(raw.get("strategy_version")))
        if raw.get("strategy_git_sha") or raw.get("git_sha"):
            shas.add(str(raw.get("strategy_git_sha") or raw.get("git_sha")))
        if raw.get("strategy_engine"):
            engines.add(str(raw.get("strategy_engine")))
    pending = any(bool(r.get("pending")) for r in affected)
    message = "LIVE loss threshold crossed: " + "; ".join(
        f"{r.get('chain')} {r.get('asset')} {r.get('pct')}%{' exit-pending' if r.get('pending') else ''}"
        for r in affected
    )
    owner = "BOTH" if pending else "STRATEGY"
    result = _v4.record_event(
        app,
        event_type="LIVE_LOSS_ALERT",
        source_component="telegram_live_loss_alert",
        message=message,
        severity="P1",
        chain=",".join(chains),
        strategy_id=",".join(sorted(engines)),
        strategy_version=",".join(sorted(versions)),
        git_sha=",".join(sorted(shas)),
        trade_ids=pids,
        financial_impact={"threshold_pct": str(threshold), "breach_count": int(count)},
        technical_impact="exit pending; investigate execution/sellability" if pending else "",
        evidence_refs=["telegram:LIVE_LOSS_ALERT"],
        owner_monitor=owner,
        allowed_actions=["REPORT", "RESEARCH", "SHADOW_PROPOSE"],
    )
    _queue_case(app, result.get("case"), result.get("event"))


def send_new_loss_alerts(app, tid) -> int:
    count = _PREV_LOSS_ALERT(app, tid)
    try:
        _loss_event(app, tid, int(count or 0))
    except Exception as exc:
        print(f"[ai-ops-v4-loss:{tid}] {type(exc).__name__}: {exc}")
    return count


def send_new_profit_alerts(app, tid) -> int:
    count = _PREV_PROFIT_ALERT(app, tid)
    if int(count or 0) > 0:
        try:
            threshold = _profit.profit_alert_threshold_pct(app, tid)
            _v4.record_event(
                app,
                event_type="STRATEGY_REPORT",
                source_component="telegram_live_profit_alert",
                message=f"LIVE profit threshold crossed for {int(count)} position(s) at +{threshold}%.",
                severity="P3",
                financial_impact={"threshold_pct": str(threshold), "crossing_count": int(count)},
                owner_monitor="STRATEGY",
                allowed_actions=["REPORT"],
            )
        except Exception as exc:
            print(f"[ai-ops-v4-profit:{tid}] {type(exc).__name__}: {exc}")
    return count


def scheduled_report_text(app, tid) -> str:
    text = _PREV_SCHEDULED_REPORT_TEXT(app, tid)
    try:
        if "⚠️" in text or "🚨" in text:
            severity = "P1" if "🚨" in text else "P2"
            result = _v4.record_event(
                app,
                event_type="WARNING",
                source_component="scheduled_capital_report",
                message=text,
                severity=severity,
                evidence_refs=["telegram:scheduled-capital-report"],
                allowed_actions=["REPORT", "RESEARCH"],
            )
            _queue_case(app, result.get("case"), result.get("event"))
    except Exception as exc:
        print(f"[ai-ops-v4-scheduled:{tid}] {type(exc).__name__}: {exc}")
    return text


def _record_transition(text: str) -> None:
    app = _APP
    if app is None:
        return
    upper = str(text or "").upper()
    if "ENGINEERING" in upper:
        et, owner = "ENGINEERING_REPORT", "ENGINEERING"
    elif "STRATEGY" in upper:
        et, owner = "STRATEGY_REPORT", "STRATEGY"
    elif "FACTORY" in upper:
        et, owner = "FACTORY_REPORT", "FACTORY"
    else:
        et, owner = "WARNING", ""
    severity = "P1" if "🚨" in text else ("P2" if "⚠️" in text else "P3")
    result = _v4.record_event(
        app,
        event_type=et,
        source_component="ai_ops_transition",
        message=text,
        severity=severity,
        owner_monitor=owner,
        evidence_refs=["ai-reviews:transition"],
        allowed_actions=["REPORT", "RESEARCH"],
    )
    _queue_case(app, result.get("case"), result.get("event"))


def transition_messages(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    for text in messages:
        try:
            _record_transition(str(text))
        except Exception as exc:
            print(f"[ai-ops-v4-transition] {type(exc).__name__}: {exc}")
    return messages


def send_to_chats(token, chat_ids, text, *args, **kwargs):
    result = _PREV_SEND_TO_CHATS(token, chat_ids, text, *args, **kwargs)
    try:
        app = _APP
        body = str(text or "")
        if app is not None and "LIVE LOSS ALERT" not in body.upper() and ("⚠️" in body or "🚨" in body):
            if "AI AGENT HEALTH WARNING" in body.upper():
                et, severity, owner = "AI_HEALTH_WARNING", "P2", "AI_HEALTH"
            else:
                et = "WARNING"
                severity = "P1" if "🚨" in body else "P2"
                owner = ""
            recorded = _v4.record_event(
                app,
                event_type=et,
                source_component="telegram_outbound_warning",
                message=body,
                severity=severity,
                owner_monitor=owner,
                evidence_refs=["telegram:outbound"],
                allowed_actions=["REPORT", "RESEARCH"],
            )
            _queue_case(app, recorded.get("case"), recorded.get("event"))
    except Exception as exc:
        print(f"[ai-ops-v4-telegram-capture] {type(exc).__name__}: {exc}")
    return result


def _events_text(app) -> str:
    rows = _v4.list_events(app, limit=10)
    lines = ["<b>⚠️ AI OPS V4 EVENTS</b>", ""]
    if not rows:
        lines.append("No V4 events recorded yet.")
    for row in reversed(rows):
        lines.append(
            f"<b>{_safe(row.get('severity'),20)} {_safe(row.get('event_type'),80)}</b> • {_safe(row.get('owner_monitor'),40)} "
            f"• x{int(row.get('occurrence_count') or 1)}\n"
            f"<code>{_safe(row.get('event_id'),80)}</code> • {_safe(row.get('message'),420)}"
        )
    lines += ["", "<i>Events are evidence records only; they cannot authorise protected trading state changes.</i>"]
    return "\n".join(lines)


def _cases_text(app) -> str:
    rows = _v4.list_cases(app, limit=10)
    lines = ["<b>🏭 AI OPS V4 CASES</b>", ""]
    if not rows:
        lines.append("No Factory/monitor cases open yet.")
    for row in reversed(rows):
        lines.append(
            f"<b>{_safe(row.get('severity'),20)} {_safe(row.get('owner_monitor'),60)}</b> • {_safe(row.get('case_status'),40)}\n"
            f"<code>{_safe(row.get('case_id'),90)}</code> • Factory {_safe(row.get('factory_analysis_status') or 'NOT_RUN',60)}\n"
            f"Allowed: {_safe(', '.join(row.get('allowed_actions') or []),240)}"
        )
    return "\n".join(lines)


def _scores_text(app) -> str:
    rows = _v4.list_scores(app, limit=10)
    lines = ["<b>⭐ AI CONTRIBUTION SCORES</b>", f"Current independent score auditor: <b>{_safe(_v4.score_auditor_for_week(),80)}</b>", ""]
    if not rows:
        lines.append("No V4 contribution scores recorded yet.")
    for row in reversed(rows):
        lines.append(
            f"<b>{_safe(row.get('agent'),60)} {int(row.get('score') or 0)}/100</b> • {_safe(row.get('status'),30)}\n"
            f"Scorer: {_safe(row.get('scorer'),60)} • Audit: {_safe(row.get('audit_status'),50)} • {_safe(row.get('category'),80)}"
        )
    lines += ["", "<i>GPT cannot score itself. Extreme/material scores require an independent auditor.</i>"]
    return "\n".join(lines)


def _gaps_text(app) -> str:
    rows = _v4.list_gap_reports(app, limit=8)
    lines = ["<b>💰 IMPLEMENTATION GAPS</b>", ""]
    if not rows:
        lines.append("No V4 implementation-gap reports recorded yet.")
    for row in reversed(rows):
        lines.append(
            f"<b>{_safe(row.get('decision'),30)} — {_safe(row.get('proposal'),220)}</b>\n"
            f"Blocked: {_safe(row.get('why_blocked'),280)}\n"
            f"Cheapest safe option: {_safe(row.get('cheapest_safe_option'),280)}"
        )
    return "\n".join(lines)


def _v4_text(app) -> str:
    rotation = _v4.engineering_rotation_for_day()
    assigned = rotation.get("assigned")
    if isinstance(assigned, list):
        assigned_text = "ALL SIX: " + ", ".join(assigned)
    else:
        assigned_text = str(assigned)
    return "\n".join([
        "<b>🧠 AI OPERATIONS CONSTITUTION V4</b>",
        "",
        "Status: <b>ACTIVE SOFTWARE LAYER</b>",
        f"Today's Engineering deep-review assignment: <b>{_safe(assigned_text,300)}</b>",
        f"Mode: <b>{_safe(rotation.get('mode'),80)}</b>",
        f"Score auditor: <b>{_safe(_v4.score_auditor_for_week(),80)}</b>",
        "",
        f"Events: <b>{len(_v4.list_events(app, limit=1000))}</b>",
        f"Cases: <b>{len(_v4.list_cases(app, limit=500))}</b>",
        f"Scores: <b>{len(_v4.list_scores(app, limit=1000))}</b>",
        f"Implementation gaps: <b>{len(_v4.list_gap_reports(app, limit=500))}</b>",
        "",
        "<i>V4 is an operations/governance layer. It does not grant authority to change LIVE, capital, wallets/signing or safety controls.</i>",
    ])


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/aievents", "/aicases", "/aiscores", "/aigaps", "/aiv4"}:
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return
            body = {
                "/aievents": _events_text,
                "/aicases": _cases_text,
                "/aiscores": _scores_text,
                "/aigaps": _gaps_text,
                "/aiv4": _v4_text,
            }[cmd](app)
            _ui._send(app, tid, body)
            return
    return _PREV_HANDLE_UPDATE(app, update)


def _app_with_v4():
    global _APP
    app = _PREV_APP()
    _APP = app
    _start_case_worker()
    return app


def install() -> None:
    if getattr(_ui, "_telegram_ai_ops_v4_installed", False):
        return
    for command in V4_COMMANDS:
        if not any(cmd == command[0] for cmd, _ in _aiops.AI_MASTER_COMMANDS):
            _aiops.AI_MASTER_COMMANDS = tuple(_aiops.AI_MASTER_COMMANDS) + (command,)
    _loss.send_new_loss_alerts = send_new_loss_alerts
    _profit.send_new_profit_alerts = send_new_profit_alerts
    _loss.scheduled_report_text = scheduled_report_text
    _aiops.transition_messages = transition_messages
    _tg.send_to_chats = send_to_chats
    _ui.handle_update = handle_update
    _cli._app = _app_with_v4
    _ui._telegram_ai_ops_v4_installed = True


install()
