from __future__ import annotations

import os
from typing import Any

from . import ai_cost_router as _cost
from . import master_change_council as _base
from .ai_cost_provider_patch import call_provider
from scripts.ai_agent_ws_worker import _PROVIDER_CALL_LOCK

_MISSING = object()


def _restore_env(key: str, previous: object | str) -> None:
    if previous is _MISSING:
        os.environ.pop(key, None)
    else:
        os.environ[key] = str(previous)


def _route_for_state(state: dict) -> dict[str, Any]:
    route = state.get("cost_route")
    if isinstance(route, dict) and route.get("advisers") is not None:
        return route
    return _cost.master_change_route(
        str(state.get("request") or ""),
        hard_protected_reasons=list(state.get("hard_protected_reasons") or []),
        protected_reasons=list(state.get("protected_reasons") or []),
    )


def _required_advisers(state: dict) -> tuple[str, ...]:
    route = _route_for_state(state)
    allowed = set(_base.ADVISERS)
    return tuple(name for name in route.get("advisers") or [] if name in allowed)


def _successful(row: dict | None) -> bool:
    row = row or {}
    rc = row.get("provider_rc")
    return bool(row.get("acknowledged")) and rc is not None and int(rc) == 0 and bool(str(row.get("reply") or "").strip())


def _gpt_decision_prompt(state: dict) -> str:
    evidence = []
    for adviser in _required_advisers(state):
        row = (state.get("advisers") or {}).get(adviser) or {}
        reused = "YES" if row.get("reused") else "NO"
        evidence.append(
            f"===== {adviser.upper()} =====\n"
            f"ACK: {bool(row.get('acknowledged'))}\n"
            f"REUSED: {reused}\n"
            f"ERROR: {row.get('error') or ''}\n"
            f"REPLY:\n{str(row.get('reply') or '')[:9000]}"
        )
    route = _route_for_state(state)
    return f"""You are GPT, final adjudicator for a MASTER-requested repository change.

First analyse the MASTER request independently. Then critically evaluate every adviser report selected by the deterministic Cost Router. Do not decide by majority vote. Reject unsupported assumptions and preserve useful minority objections.

MASTER REQUEST ID: {state.get('request_id')}
SOURCE SHA: {state.get('source_sha')}
COST ROUTE: Level {route.get('level')} — {route.get('reason')}
REQUIRED ADVISERS: {list(_required_advisers(state))}
MASTER REQUEST:
{state.get('request')}

DETERMINISTIC HARD-PROTECTION SIGNALS: {state.get('hard_protected_reasons') or []}
DETERMINISTIC PROTECTED/RISK SIGNALS: {state.get('protected_reasons') or []}

ADVISER REPORTS:
{chr(10).join(evidence)}

Return ONLY one JSON object with this schema:
{{
  "action":"IMPLEMENT|REJECT|HUMAN_REVIEW",
  "risk_class":"LOW|MEDIUM|HIGH|CRITICAL",
  "summary":"short operator summary",
  "reasoning":"why this decision is justified",
  "allowed_files":["exact/repository/path.py"],
  "required_tests":["specific test command or test name"],
  "auto_merge_recommended":false
}}

Rules:
- IMPLEMENT means GPT believes a bounded code change should be attempted.
- allowed_files must be exact repository paths, not globs, and no more than 20 paths.
- Never put secrets, private keys, seed phrases or credentials in any field.
- HARD-protected requests should normally be HUMAN_REVIEW or REJECT; this council is not a wallet/signing/secrets authority.
- Trading/LIVE/risk/deployment changes may be IMPLEMENT only as a draft-PR change; never recommend automatic live deployment.
- auto_merge_recommended may be true only for clearly LOW-risk presentation/reporting/tests/docs changes.
"""


def _call_final_gpt(state: dict) -> tuple[int, str, str]:
    route = _route_for_state(state)
    configured = str(os.environ.get("AI_MASTER_CHANGE_GPT_MODEL") or "").strip()
    model = configured or str(route.get("gpt_model") or _base.DEFAULT_GPT_MODEL).strip()
    level = max(1, min(int(route.get("level") or 1), 4))
    with _PROVIDER_CALL_LOCK:
        previous_model: object | str = os.environ.get("OPENAI_COUNCIL_MODEL", _MISSING)
        previous_kind: object | str = os.environ.get("AI_COST_TASK_KIND", _MISSING)
        previous_level: object | str = os.environ.get("AI_COST_ROUTE_LEVEL", _MISSING)
        os.environ["OPENAI_COUNCIL_MODEL"] = model
        os.environ["AI_COST_TASK_KIND"] = "master-change-final"
        os.environ["AI_COST_ROUTE_LEVEL"] = str(level)
        try:
            return call_provider("gpt", _gpt_decision_prompt(state))
        finally:
            _restore_env("OPENAI_COUNCIL_MODEL", previous_model)
            _restore_env("AI_COST_TASK_KIND", previous_kind)
            _restore_env("AI_COST_ROUTE_LEVEL", previous_level)


def _process(app, request_id: str) -> None:
    try:
        state = _base.load_request(app, request_id)
        if not state:
            return

        route = _cost.master_change_route(
            str(state.get("request") or ""),
            hard_protected_reasons=list(state.get("hard_protected_reasons") or []),
            protected_reasons=list(state.get("protected_reasons") or []),
        )
        required = tuple(route.get("advisers") or [])
        state["cost_route"] = route
        state["required_advisers"] = list(required)
        state["request_fingerprint"] = _cost.request_fingerprint(str(state.get("request") or ""))
        state["status"] = "REVIEWING"
        state["attempt"] = int(state.get("attempt") or 0) + 1
        state.setdefault("advisers", {})
        _base._write_state(app, state)

        saved = max(0, 5 - int(route.get("model_calls_before_implementation") or 0))
        _base._notify(
            app,
            state.get("requester_chat_id"),
            f"💰 AI COST ROUTE L{route.get('level')} — {request_id}\n"
            f"Agents: {', '.join(required) or 'none'} → GPT final.\n"
            f"Planned model calls: {route.get('model_calls_before_implementation')} (saving {saved} vs old five-call council).\n"
            f"{route.get('reason')}",
        )

        for adviser in required:
            old = (state.get("advisers") or {}).get(adviser) or {}
            if _successful(old):
                state["advisers"][adviser] = {**old, "reused": True}
                _base._write_state(app, state)
                continue
            result = _base._ask_adviser(
                adviser,
                request_id,
                str(state.get("request") or ""),
                str(state.get("source_sha") or ""),
                int(state["attempt"]),
            )
            result["reused"] = False
            state["advisers"][adviser] = result
            _base._write_state(app, state)

        all_ok = all(_successful((state.get("advisers") or {}).get(name)) for name in required)
        state["all_advisers_replied"] = all_ok
        if not all_ok:
            state["status"] = "INCOMPLETE"
            state["implementation_allowed"] = False
            state["cost_snapshot"] = _cost.snapshot()
            _base._write_state(app, state)
            failed = [name for name in required if not _successful((state.get("advisers") or {}).get(name))]
            _base._notify(
                app,
                state.get("requester_chat_id"),
                f"⚠️ AI CHANGE {request_id}\nRequired adviser incomplete: {', '.join(failed) or 'unknown'}. "
                f"GPT implementation is blocked. Successful adviser replies are cached; retry will not pay for them again.",
            )
            return

        rc, out, err = _call_final_gpt(state)
        if rc != 0 or not str(out or "").strip():
            state["status"] = "GPT_FAILED"
            state["gpt_error"] = str(err or f"GPT rc={rc}")[:1200]
            state["implementation_allowed"] = False
            state["cost_snapshot"] = _cost.snapshot()
            _base._write_state(app, state)
            _base._notify(
                app,
                state.get("requester_chat_id"),
                f"⚠️ AI CHANGE {request_id}\nRequired advisers are complete, but GPT final decision failed. "
                f"Retry reuses successful adviser replies to avoid duplicate spend.",
            )
            return

        decision = _base._normalise_decision(_base._extract_json(out))
        state["gpt_decision"] = decision
        hard = list(state.get("hard_protected_reasons") or [])
        implementation_allowed = decision["action"] == "IMPLEMENT" and bool(decision["allowed_files"]) and not hard
        state["implementation_allowed"] = implementation_allowed
        state["auto_merge_allowed"] = bool(
            implementation_allowed
            and decision["risk_class"] == "LOW"
            and decision["auto_merge_recommended"]
            and not state.get("protected_reasons")
        )
        state["implementation_nonce"] = int(state.get("implementation_nonce") or 0) + (1 if implementation_allowed else 0)
        state["bridge_revision"] = int(state.get("bridge_revision") or 0) + 1
        state["cost_snapshot"] = _cost.snapshot()
        if implementation_allowed:
            state["status"] = "READY_FOR_IMPLEMENTATION"
        elif decision["action"] == "REJECT":
            state["status"] = "REJECTED"
        else:
            state["status"] = "HUMAN_REVIEW"
        _base._write_state(app, state)

        summary = decision.get("summary") or decision.get("reasoning") or "No summary"
        if implementation_allowed:
            next_step = "GPT implementation is authorised for a bounded branch/PR."
            if state.get("auto_merge_allowed"):
                next_step += " LOW-risk auto-merge is eligible after tests and path gates."
            else:
                next_step += " Automatic merge is blocked; the result will remain a draft PR."
        else:
            next_step = "No repository implementation will run from this request."
        cost = state.get("cost_snapshot") or {}
        _base._notify(
            app,
            state.get("requester_chat_id"),
            f"🧠 GPT MASTER DECISION — {request_id}\n"
            f"Cost route: L{route.get('level')} | Decision: {decision['action']} | Risk: {decision['risk_class']}\n"
            f"{summary[:1000]}\n\n{next_step}\n"
            f"AI spend today: ${float(cost.get('daily_usd') or 0):.4f} / ${float(cost.get('daily_budget_usd') or 0):.2f}",
        )
    except Exception as exc:
        state = _base.load_request(app, request_id)
        if state:
            state["status"] = "FAILED"
            state["error"] = f"{type(exc).__name__}: {exc}"[:1200]
            state["implementation_allowed"] = False
            try:
                state["cost_snapshot"] = _cost.snapshot()
            except Exception:
                pass
            _base._write_state(app, state)
            _base._notify(app, state.get("requester_chat_id"), f"⚠️ AI CHANGE {request_id} failed: {type(exc).__name__}: {exc}")
    finally:
        with _base._WORKER_LOCK:
            _base._RUNNING.discard(request_id)


def submit(app, requester_chat_id: str | int, request: str) -> dict:
    text = _base._clean_request(request)
    hard, protected = _base.protection_reasons(text)
    route = _cost.master_change_route(text, hard_protected_reasons=hard, protected_reasons=protected)
    request_id = _base._new_request_id()
    now = int(__import__("time").time())
    state = {
        "schema_version": 2,
        "request_id": request_id,
        "request": text,
        "request_fingerprint": _cost.request_fingerprint(text),
        "requester_chat_id": str(requester_chat_id),
        "source_sha": _base._source_sha(),
        "created_epoch": now,
        "updated_epoch": now,
        "status": "QUEUED",
        "attempt": 0,
        "hard_protected_reasons": hard,
        "protected_reasons": protected,
        "cost_route": route,
        "required_advisers": list(route.get("advisers") or []),
        "advisers": {},
        "all_advisers_replied": False,
        "gpt_decision": {},
        "implementation_allowed": False,
        "auto_merge_allowed": False,
        "implementation_nonce": 0,
        "bridge_revision": 1,
        "cost_snapshot": _cost.snapshot(),
    }
    _base._write_state(app, state)
    _base._enqueue(app, request_id)
    return state


def status_text(state: dict) -> str:
    if not state:
        return "No MASTER AI change request exists yet."
    route = _route_for_state(state)
    required = set(_required_advisers(state))
    advisers = state.get("advisers") or {}
    rows = []
    for name in _base.ADVISERS:
        row = advisers.get(name) or {}
        if name not in required:
            mark, suffix = "⚪", "not required"
        elif _successful(row):
            mark = "♻️" if row.get("reused") else "✅"
            suffix = "reused" if row.get("reused") else "complete"
        elif row:
            mark, suffix = "⚠️", "failed"
        else:
            mark, suffix = "⏳", "waiting"
        rows.append(f"{mark} {name.title()} — {suffix}")
    decision = state.get("gpt_decision") or {}
    cost = state.get("cost_snapshot") or {}
    lines = [
        f"🧠 AI CHANGE {state.get('request_id')}",
        f"Status: {state.get('status')}",
        f"Cost route: L{route.get('level')} — {route.get('reason')}",
        f"Planned model calls: {route.get('model_calls_before_implementation')}",
        f"Source: {str(state.get('source_sha') or '')[:12]}",
        *rows,
    ]
    if decision:
        lines += [f"GPT: {decision.get('action')} / {decision.get('risk_class')}", str(decision.get("summary") or "")[:900]]
    if cost:
        lines.append(f"AI cost today: ${float(cost.get('daily_usd') or 0):.4f} / ${float(cost.get('daily_budget_usd') or 0):.2f}")
    if state.get("protected_reasons"):
        lines.append("Protected lane: automatic merge disabled.")
    if state.get("hard_protected_reasons"):
        lines.append("Hard-protected lane: implementation blocked; use the dedicated security/wallet workflow.")
    return "\n".join(x for x in lines if x)


def install() -> None:
    if getattr(_base, "_cost_router_patch_installed", False):
        return
    _base._gpt_decision_prompt = _gpt_decision_prompt
    _base._call_final_gpt = _call_final_gpt
    _base._process = _process
    _base.submit = submit
    _base.status_text = status_text
    _base._cost_router_patch_installed = True


install()
