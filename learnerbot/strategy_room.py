from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "copilot")
ACTIONS = {"NONE", "DRAFT_SHADOW_CHANGE", "HUMAN_APPROVAL_REQUIRED"}
MAX_TASK_CHARS = 1200

_ACTION_RE = re.compile(r"(?im)^STRATEGY_ROOM_ACTION:\s*(NONE|DRAFT_SHADOW_CHANGE|HUMAN_APPROVAL_REQUIRED)\s*$")
_TASK_RE = re.compile(r"(?im)^STRATEGY_ROOM_TASK:\s*(.*)$")


def _clean_task(value: object) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text[:MAX_TASK_CHARS]


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _bridge_path() -> Path:
    return Path("/var/tmp/boot/strategy_room_request.json")


def _local_state_path(app) -> Path:
    return Path(app.data_dir) / "strategy_room" / "latest_request.json"


def load_request(app) -> dict:
    for path in (_local_state_path(app), _bridge_path()):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    return {}


def queue_draft_shadow_change(
    app,
    *,
    task: str,
    question: str,
    session_id: str,
    requested_by: str | int,
    support_count: int,
) -> dict:
    """Queue a bounded GPT draft implementation request.

    This bridge is deliberately incapable of merging, deploying or touching LIVE,
    risk, capital, CSV, wallet/signing or workflow files. The GitHub worker applies
    the final allow-list and opens a draft PR only.
    """
    support_count = int(support_count or 0)
    if support_count < 3:
        raise ValueError("Strategy Room draft implementation requires at least three completed agent reviews")
    clean_task = _clean_task(task)
    if not clean_task:
        raise ValueError("Strategy Room draft implementation needs a concrete task")
    previous = load_request(app)
    nonce = max(int(previous.get("nonce") or 0) + 1, int(time.time() * 1000))
    value = {
        "schema_version": 1,
        "action": "draft_shadow_fix",
        "nonce": nonce,
        "task": clean_task,
        "question": _clean_task(question)[:800],
        "session_id": str(session_id or "")[:64],
        "support_count": support_count,
        "requested_by": str(requested_by or "")[:80],
        "requested_epoch": int(time.time()),
        "draft_pr_only": True,
        "no_live_changes": True,
    }
    _atomic_json(_local_state_path(app), value)
    try:
        _atomic_json(_bridge_path(), value)
        os.chmod(_bridge_path(), 0o644)
    except Exception as exc:
        print(f"[strategy-room-bridge] {type(exc).__name__}: {exc}")
    return value


def build_gpt_leader_prompt(session: dict) -> str:
    question = str(session.get("question") or "")
    blocks: list[str] = []
    for provider in PROVIDERS:
        row = (session.get("answers") or {}).get(provider) or {}
        if str(row.get("status") or "") != "DONE":
            continue
        answer = str(row.get("answer") or "").strip()
        if answer:
            blocks.append(f"===== {provider.upper()} =====\n{answer}")
    evidence = "\n\n".join(blocks)
    if len(evidence) > 42000:
        evidence = evidence[:42000]

    return f"""You are GPT acting as the Strategy Room leader for this trading-bot operator.

Use the independent agent reviews below as advisory evidence. Do not decide by majority vote; critically compare them. Give one practical conclusion for the operator.

IMPLEMENTATION POLICY:
- If no code change is justified, choose NONE.
- If a useful change is justified and can be implemented entirely as LOW/MEDIUM-risk SHADOW/Strategy-Lab work, choose DRAFT_SHADOW_CHANGE and give one concise implementation task.
- Any change to LIVE execution, trading thresholds, stop-loss/take-profit, capital, risk limits, wallet/signing, private keys, CSV runtime settings, deployment, GitHub Actions, or automatic merging must choose HUMAN_APPROVAL_REQUIRED.
- Never claim an unavailable agent agreed.
- The draft implementation worker is allow-listed and can only prepare a draft PR; it cannot merge or deploy.

Write a clear mobile-friendly answer first. At the very end add exactly these two machine lines:
STRATEGY_ROOM_ACTION: NONE|DRAFT_SHADOW_CHANGE|HUMAN_APPROVAL_REQUIRED
STRATEGY_ROOM_TASK: <one concise task, or NONE>

OPERATOR QUESTION:
{question}

INDEPENDENT REVIEWS:
{evidence or '[No usable independent reviews.]'}
"""


def parse_gpt_leader_output(text: str) -> tuple[str, str, str]:
    raw = str(text or "").strip()
    action_match = _ACTION_RE.search(raw)
    task_match = _TASK_RE.search(raw)
    action = action_match.group(1).upper() if action_match else "NONE"
    if action not in ACTIONS:
        action = "NONE"
    task = _clean_task(task_match.group(1) if task_match else "")
    visible = _ACTION_RE.sub("", raw)
    visible = _TASK_RE.sub("", visible).strip()
    return visible, action, task


def strategy_room_agent_health(root: Path | str, now: int | None = None, *, max_age_seconds: int = 86400) -> dict:
    """Summarise latest Strategy Room/Council result per provider from local mailbox sessions."""
    root = Path(root)
    now = int(now or time.time())
    session_dir = root / "data" / "ai_council"
    latest = {p: {"state": "WAITING", "reason": "No Strategy Room request recorded", "updated_epoch": 0} for p in PROVIDERS}
    try:
        paths = sorted(session_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    except Exception:
        paths = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(value, dict) or str(value.get("mode") or "") != "strategy_room":
            continue
        updated = int(value.get("updated_epoch") or value.get("created_epoch") or 0)
        answers = value.get("answers") or {}
        for provider in PROVIDERS:
            if latest[provider]["updated_epoch"]:
                continue
            row = answers.get(provider) or {}
            status = str(row.get("status") or "").upper()
            if not status:
                continue
            age = max(0, now - updated) if updated else max_age_seconds + 1
            if age > max_age_seconds:
                state, reason = "WAITING", "Last Strategy Room result is stale"
            elif status == "DONE":
                state, reason = "WORKING", "Latest Strategy Room reply completed"
            else:
                state = "FAILED"
                reason = str(row.get("error") or f"Latest Strategy Room reply status {status}")[:240]
            latest[provider] = {"state": state, "reason": reason, "updated_epoch": updated, "age_seconds": age}
        if all(latest[p]["updated_epoch"] for p in PROVIDERS):
            break
    return {"agents": latest, "generated_epoch": now}


def loss_monitor_plan(app, telegram_id: str | int) -> str:
    """Human-readable current loss-monitoring plan; no settings are changed here."""
    try:
        from . import profit_control_loop_patch as _profit_loop
        profile = str(_profit_loop.active_profile(app) or "BASELINE")
    except Exception:
        profile = "UNKNOWN"
    try:
        from . import solana_execution_fault_counter_patch as _faults
        faults = int(_faults.fault_count(app, telegram_id))
    except Exception:
        faults = 0
    try:
        from . import solana_sibot as _sol
        cfg = _sol.settings(app)
    except Exception:
        cfg = {}

    loss_streak = str(cfg.get("max_consecutive_copied_losses") or "2")
    suspend = str(cfg.get("leader_suspend_minutes") or "1440")
    min_copied = str(cfg.get("min_copied_trades_for_guard") or "2")
    copied_wr = str(cfg.get("min_copied_win_rate_pct") or "50")
    copied_pf = str(cfg.get("min_copied_profit_factor") or "1.50")
    stop_loss = str(cfg.get("stop_loss_pct") or cfg.get("live_stop_loss_pct") or "10")
    fault_limit = str(cfg.get("live_no_output_disable_after") or "2")

    return (
        "<b>🛡️ STRATEGY ROOM — LOSS MONITOR</b>\n\n"
        "<b>What is already automatic</b>\n"
        "• Every entry must still pass the positive executable-edge, liquidity, simulation and reserve gates.\n"
        f"• Position stop-loss protection remains at the configured level (currently about <b>{stop_loss}%</b>).\n"
        f"• After <b>{min_copied}</b> closed LIVE copies, a leader must keep copied win rate ≥ <b>{copied_wr}%</b> and copied PF ≥ <b>{copied_pf}</b>.\n"
        f"• <b>{loss_streak}</b> consecutive copied losses suspend that leader for <b>{suspend} minutes</b>.\n"
        f"• Repeated landed-invalid Solana executions disable that user's Solana LIVE after <b>{fault_limit}</b> faults; current counted faults: <b>{faults}</b>.\n"
        f"• The hourly profit-control loop is active with profile <b>{profile}</b>; it can tighten only bounded entry/selection quality settings and cannot change capital or signing safety.\n\n"
        "<b>Escalation plan</b>\n"
        "1. Bad individual trade → normal stop/managed exit.\n"
        "2. Bad leader → quarantine/suspension; other qualified leaders may continue.\n"
        "3. Repeated execution faults → Solana LIVE is automatically disabled for the affected user.\n"
        "4. Strategy-wide deterioration → Strategy Room reviews realised net P&L, PF, realised-vs-modelled edge, rejection reasons and leader suspensions before proposing any threshold change.\n"
        "5. Platform-wide hard stop remains an explicit operator decision unless an existing deterministic execution-safety circuit breaker fires.\n\n"
        "<i>This page monitors and explains the existing protections. It does not silently alter LIVE, risk, capital or thresholds.</i>"
    )
