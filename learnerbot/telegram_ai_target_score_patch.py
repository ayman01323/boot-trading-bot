from __future__ import annotations

import html

from . import ai_agent_target_score as _score
from . import telegram_ai_reports_menu_patch as _menu
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from .user_registry import is_master

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_AI_KEYBOARD = _menu._ai_keyboard

_LABELS = {
    "gpt": "GPT",
    "claude-general": "Claude General",
    "claude-coding": "Claude Coding",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "grok": "Grok",
    "kimi": "Kimi",
    "copilot": "Copilot",
}


def _is_master(app, chat_id) -> bool:
    try:
        return bool(app is not None and chat_id is not None and is_master(app.csv_dir, chat_id))
    except Exception:
        return False


def _ai_keyboard() -> dict:
    kb = _PREV_AI_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    if any(any(str(button.get("callback_data") or "") == "aiops:scores" for button in row) for row in rows):
        return kb
    insert_at = max(0, len(rows) - 1)
    rows.insert(insert_at, [{"text": "⭐ AI Target Scores", "callback_data": "aiops:scores"}])
    return {"inline_keyboard": rows}


def _score_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔄 Refresh scores", "callback_data": "aiops:scores"}],
            [{"text": "⬅️ AI Reports", "callback_data": "menu:aiops"}],
        ]
    }


def _band_icon(band: str) -> str:
    value = str(band or "COLLECTING").upper()
    if value in {"CORE", "KEEP"}:
        return "🟢"
    if "SPECIALIST" in value or value == "COLLECTING":
        return "🟡"
    if value == "REDUCE":
        return "🟠"
    if value == "REMOVE CANDIDATE":
        return "🔴"
    return "⚪"


def _fmt(value, *, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}{suffix}"


def score_text(app) -> str:
    report = _score.summary(app)
    agents = report.get("agents") or {}
    lines = [
        "<b>⭐ AI TARGET CONTRIBUTION SCORES</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>Target:</b> improve risk-adjusted net profit and expectancy; increase PF/value edge and win frequency without worsening drawdown/tail risk.",
        "",
    ]
    for agent in _score.LOGICAL_AGENTS:
        row = agents.get(agent) or {}
        band = str(row.get("band") or "COLLECTING")
        icon = _band_icon(band)
        atcs = _fmt(row.get("atcs_90d"))
        economic = _fmt(row.get("economic_90d"), suffix="/30")
        avs = _fmt(row.get("avs"))
        lines.append(f"{icon} <b>{html.escape(_LABELS.get(agent, agent))}</b>")
        lines.append(f"ATCS 90d: <b>{atcs}</b> • Economic: <b>{economic}</b> • AVS: <b>{avs}</b>")
        lines.append(
            "Evidence: "
            f"{int(row.get('scored_contributions') or 0)} scored / "
            f"{int(row.get('outcome_resolved') or 0)} outcome-resolved • "
            f"{int(row.get('pending_score') or 0)} pending • "
            f"{int(row.get('audit_pending') or 0)} audit pending"
        )
        lines.append(f"Status: <b>{html.escape(band)}</b>")
        lines.append("")
    lines += [
        "<b>ATCS = 100 points</b>",
        "Economic impact 30 • Correctness 20 • Evidence/falsifiability 15 • Marginal value 10 • Actionability 10 • Collaboration 5 • Cost efficiency 5 • Timeliness/reliability 5.",
        "",
        "<b>Economic 30</b>: net edge/expectancy 15 • validated loss/tail-risk prevention 8 • PF/value-edge + win-frequency quality 7.",
        "Before an outcome is measured, economic credit is capped at 10/30. UNKNOWN is not converted into invented profit attribution.",
        "",
        "<b>AVS retention bands</b>: 80+ CORE • 65-79 KEEP • 50-64 SPECIALIST/PROBATION • 35-49 REDUCE • &lt;35 REMOVE CANDIDATE.",
        "",
        "<i>No score can automatically remove an agent. Removal requires ≥90 days or ≥30 material outcome-resolved decisions, two weak windows, blind holdout/ablation evidence, no critical unique specialisation, and independent audit.</i>",
    ]
    return "\n".join(lines)


def _answer(app, cb, text="") -> None:
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        data = str(cb.get("data") or "")
        if data == "aiops:scores":
            tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
            if not _is_master(app, tid):
                _answer(app, cb, "MASTER only")
                return
            _answer(app, cb)
            _sibot_ui._render(app, tid, score_text(app), _score_keyboard(), cb)
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_ai_target_score_patch_installed", False):
        return
    _menu._ai_keyboard = _ai_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_ai_target_score_patch_installed = True


install()
