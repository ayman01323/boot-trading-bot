from __future__ import annotations

from . import ai_agent_target_score as _score
from . import master_change_cost_router_patch as _cost_router
from . import master_change_council as _council
from .config import AppSettings

_PREV_ASK_ADVISER = _council._ask_adviser
_PREV_CALL_FINAL_GPT = _cost_router._call_final_gpt


def _settings():
    return AppSettings.load()


def _successful(row: dict | None) -> bool:
    row = row or {}
    try:
        rc_ok = row.get("provider_rc") is not None and int(row.get("provider_rc")) == 0
    except Exception:
        rc_ok = False
    return bool(row.get("acknowledged")) and rc_ok and bool(str(row.get("reply") or "").strip())


def _ask_adviser(adviser: str, request_id: str, request: str, source_sha: str, attempt: int):
    result = _PREV_ASK_ADVISER(adviser, request_id, request, source_sha, attempt)
    if _successful(result):
        try:
            _score.register_pending(
                _settings(),
                agent=str(adviser),
                contribution_id=f"master-change:{request_id}:{adviser}",
                category="engineering/factory",
                case_id=request_id,
                source_sha=source_sha,
                role="independent council adviser",
                evidence_refs=[str(result.get("message_id") or "")],
            )
        except Exception as exc:
            print(f"[ai-agent-score-capture] adviser {adviser}: {type(exc).__name__}: {exc}")
    return result


def _call_final_gpt(state: dict):
    result = _PREV_CALL_FINAL_GPT(state)
    rc, out, _err = result
    if int(rc or 0) == 0 and str(out or "").strip():
        try:
            _score.register_pending(
                _settings(),
                agent="gpt",
                contribution_id=f"master-change:{state.get('request_id')}:gpt-final",
                category="engineering/factory",
                case_id=str(state.get("request_id") or ""),
                source_sha=str(state.get("source_sha") or ""),
                role="final synthesis/adjudication",
            )
        except Exception as exc:
            print(f"[ai-agent-score-capture] gpt: {type(exc).__name__}: {exc}")
    return result


def install() -> None:
    if getattr(_council, "_ai_agent_target_score_capture_installed", False):
        return
    _council._ask_adviser = _ask_adviser
    _cost_router._call_final_gpt = _call_final_gpt
    _council._ai_agent_target_score_capture_installed = True


install()
