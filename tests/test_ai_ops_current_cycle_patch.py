from __future__ import annotations

from pathlib import Path

from learnerbot import ai_ops_current_cycle_patch as patch
from learnerbot import ai_ops_status as status


def _current_status():
    return {
        "available": True,
        "cycle_id": "abc123def456-2026082110-11223344",
        "source_commit": "abc123def4560000000000000000000000000000",
        "evidence_sha256": "11223344" + "0" * 56,
        "gpt": "DONE",
        "gemini": "DONE",
        "copilot": "BLOCKED_AUTH",
        "master_decision_available": False,
    }


def test_stale_global_master_is_not_shown_as_current(monkeypatch):
    current = _current_status()
    stale = {
        "cycle_id": "oldoldoldold-2026082106-aabbccdd",
        "source_commit": "old" * 13 + "o",
        "evidence_sha256": "aa" * 32,
        "status": "AVAILABLE",
        "decisions": [{"disposition": "ACCEPT"}],
    }

    def fake_read_json(repo_root, path):
        if path == "strategy/latest_status.json":
            return dict(current)
        if path.endswith("/claude.json"):
            return None
        if path.endswith("/master_decision.json") and "runs/" in path:
            return None
        if path == "strategy/latest_master_decision.json":
            return stale
        return None

    monkeypatch.setattr(status, "read_json", fake_read_json)
    out = patch.strategy_status_current_cycle(Path("."))
    assert out["master_decision_available"] is False
    assert out["master_status"] == "WAITING"
    assert out["decision_counts"] == {"ACCEPT": 0, "REJECT": 0, "DEFER": 0}
    assert out["claude"] == "WAITING"


def test_cycle_local_master_and_claude_are_reported(monkeypatch):
    current = _current_status()
    matching_master = {
        "cycle_id": current["cycle_id"],
        "source_commit": current["source_commit"],
        "evidence_sha256": current["evidence_sha256"],
        "status": "HUMAN_REVIEW_REQUIRED",
        "decisions": [
            {"disposition": "ACCEPT"},
            {"disposition": "DEFER"},
        ],
    }
    claude = {
        "provider": "claude",
        "cycle_id": current["cycle_id"],
        "source_commit": current["source_commit"],
        "evidence_sha256": current["evidence_sha256"],
        "status": "CHANGES_PROPOSED",
    }

    def fake_read_json(repo_root, path):
        if path == "strategy/latest_status.json":
            return dict(current)
        if path.endswith("/claude.json"):
            return claude
        if path.endswith("/master_decision.json") and "runs/" in path:
            return matching_master
        if path == "strategy/latest_master_decision.json":
            raise AssertionError("cycle-local master must be preferred")
        return None

    monkeypatch.setattr(status, "read_json", fake_read_json)
    out = patch.strategy_status_current_cycle(Path("."))
    assert out["claude"] == "DONE"
    assert out["master_decision_available"] is True
    assert out["master_status"] == "HUMAN_REVIEW_REQUIRED"
    assert out["master_cycle_id"] == current["cycle_id"]
    assert out["decision_counts"] == {"ACCEPT": 1, "REJECT": 0, "DEFER": 1}
