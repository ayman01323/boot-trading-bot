from __future__ import annotations

import json
from pathlib import Path

from learnerbot import ai_agent_health_master_reconcile_patch as patch


CYCLE = "154d417006d3-2026082023-e5d4a14f"
SOURCE = "154d417006d3461fa90b89b7c41f6a5cb5b6424d"
EVIDENCE = "e5d4a14fbe859ff99269688657fd6077e3e2917327bae584d4e19c535139d840"


def _write(root: Path, rel: str, value: dict) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _base(root: Path) -> None:
    _write(
        root,
        "strategy/latest_status.json",
        {
            "available": True,
            "cycle_id": CYCLE,
            "source_commit": SOURCE,
            "evidence_sha256": EVIDENCE,
            "gpt": "DONE",
            "gemini": "INCOMPLETE",
            "copilot": "BLOCKED_AUTH",
            "master_decision_available": False,
        },
    )
    _write(
        root,
        f"strategy/runs/{CYCLE}/context.json",
        {"source_commit": SOURCE, "evidence_sha256": EVIDENCE},
    )
    _write(
        root,
        f"strategy/runs/{CYCLE}/gpt.json",
        {
            "provider": "gpt",
            "cycle_id": CYCLE,
            "source_commit": SOURCE,
            "evidence_sha256": EVIDENCE,
            "status": "CHANGES_PROPOSED",
        },
    )
    _write(
        root,
        f"strategy/runs/{CYCLE}/gemini.json",
        {
            "provider": "gemini",
            "cycle_id": CYCLE,
            "source_commit": SOURCE,
            "evidence_sha256": EVIDENCE,
            "status": "INCOMPLETE",
            "evidence_gaps": ["quota 429"],
        },
    )


def test_partial_master_is_reported_as_decided_even_when_primary_status_is_stale(tmp_path: Path) -> None:
    _base(tmp_path)
    _write(
        tmp_path,
        f"strategy/runs/{CYCLE}/master_decision.json",
        {
            "cycle_id": CYCLE,
            "source_commit": SOURCE,
            "evidence_sha256": EVIDENCE,
            "status": "HUMAN_REVIEW_REQUIRED",
        },
    )
    result = patch._strategy_health_reconciled(tmp_path, 1787270400)
    assert result["master"] == "DECIDED_PARTIAL"
    assert result["agents"]["gpt"]["state"] == "WORKING"
    assert result["agents"]["gemini"]["state"] == "NOT_WORKING"


def test_reconciled_copilot_assignment_overrides_stale_blocked_auth(tmp_path: Path) -> None:
    _base(tmp_path)
    now = 1787270400
    _write(
        tmp_path,
        f"strategy/runs/{CYCLE}/copilot_assignment_reconciled.json",
        {
            "assignment_state": "ASSIGNED",
            "reason": "Copilot assignment verified",
            "checked_epoch": now - 60,
        },
    )
    result = patch._strategy_health_reconciled(tmp_path, now)
    assert result["agents"]["copilot"]["state"] == "WAITING"
    assert "assigned" in result["agents"]["copilot"]["reason"].lower()
