from __future__ import annotations

import csv
from pathlib import Path

from learnerbot.ai_ops_status import decision_counts, master_chat_ids, transition_messages


def _write_users(root: Path, rows: list[dict]):
    path = root / "users.csv"
    headers = [
        "telegram_id", "role", "status", "fee_plan_id", "label", "allowed_chains",
        "max_wallets", "can_transfer", "can_manual_trade", "can_auto_trade",
        "created_epoch", "activated_epoch", "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in headers})


def test_master_chat_ids_are_dynamic_and_active_only(tmp_path):
    _write_users(tmp_path, [
        {"telegram_id": "100", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "200", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "300", "role": "MASTER", "status": "SUSPENDED"},
        {"telegram_id": "400", "role": "MASTER", "status": "ACTIVE"},
    ])
    assert master_chat_ids(tmp_path) == ["100", "400"]


def test_decision_counts_show_gpt_agreed_refused_and_deferred():
    master = {
        "decisions": [
            {"disposition": "ACCEPT"},
            {"disposition": "REJECT"},
            {"disposition": "REJECT"},
            {"disposition": "DEFER"},
        ]
    }
    assert decision_counts(master) == {"ACCEPT": 1, "REJECT": 2, "DEFER": 1}


def test_transition_messages_report_each_meaningful_engineering_stage_once():
    previous = {
        "engineering": {
            "available": True,
            "source_commit": "abc",
            "gpt": "WAITING",
            "gemini": "WAITING",
            "copilot": "WAITING",
            "three_agent_reports_complete": False,
            "master_decision_available": False,
            "decision_counts": {"ACCEPT": 0, "REJECT": 0, "DEFER": 0},
            "corrective_pr_url": "",
        },
        "strategy": {"available": False},
    }
    current = {
        "engineering": {
            "available": True,
            "source_commit": "abc",
            "gpt": "DONE",
            "gemini": "DONE",
            "copilot": "DONE",
            "three_agent_reports_complete": True,
            "master_decision_available": True,
            "master_status": "DRAFT_FIX",
            "decision_counts": {"ACCEPT": 2, "REJECT": 1, "DEFER": 1},
            "policy_accepted_count": 1,
            "corrective_pr_url": "https://example.invalid/pr/1",
        },
        "strategy": {"available": False},
    }
    messages = transition_messages(previous, current)
    joined = "\n".join(messages)
    assert "THREE ENGINEERING AGENTS COMPLETE" in joined
    assert "ACCEPT 2 | REJECT 1 | DEFER 1" in joined
    assert "CORRECTIVE ACTION READY" in joined
    assert "private" not in joined.lower()


def test_strategy_notifications_are_shadow_gated():
    previous = {"engineering": {}, "strategy": {"available": False}}
    current = {
        "engineering": {},
        "strategy": {
            "available": True,
            "cycle_id": "cycle-1",
            "gpt": "DONE",
            "gemini": "DONE",
            "copilot": "DONE",
            "three_agent_reports_complete": True,
            "master_decision_available": True,
            "decision_counts": {"ACCEPT": 1, "REJECT": 0, "DEFER": 0},
            "change_pr_url": "https://example.invalid/pr/2",
        },
    }
    joined = "\n".join(transition_messages(previous, current))
    assert "THREE-AGENT STRATEGY REVIEW STARTED" in joined
    assert "THREE STRATEGY AGENTS COMPLETE" in joined
    assert "shadow-first" in joined
    assert "no automatic live deployment" in joined
