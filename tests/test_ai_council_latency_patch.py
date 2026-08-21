from __future__ import annotations

import time
from types import SimpleNamespace

from learnerbot import ai_council_latency_patch as latency


def test_slow_reviewer_does_not_block_completed_reviewers(monkeypatch, tmp_path) -> None:
    app = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr(latency._council, "PROVIDERS", ("fast", "slow"))
    monkeypatch.setattr(latency, "_review_budget_seconds", lambda: 0.03)

    def fake_call(provider: str, question: str):
        if provider == "slow":
            time.sleep(0.15)
        return provider, {
            "status": "DONE",
            "answer": provider,
            "error": "",
            "return_code": 0,
            "duration_ms": 1,
        }

    monkeypatch.setattr(latency._council, "_call_independent", fake_call)
    session = latency._council.create_session(app, 123, "test", mode="user")

    started = time.monotonic()
    result = latency.run_independent_answers(app, session["session_id"])
    elapsed = time.monotonic() - started

    assert elapsed < 0.12
    assert result["answers"]["fast"]["status"] == "DONE"
    assert result["answers"]["slow"]["status"] == "FAILED"
    assert "deadline" in result["answers"]["slow"]["error"]


def test_review_budget_has_safe_bounds(monkeypatch) -> None:
    monkeypatch.setenv("PASPUSS_REVIEW_BUDGET_SECONDS", "1")
    assert latency._review_budget_seconds() == 5.0
    monkeypatch.setenv("PASPUSS_REVIEW_BUDGET_SECONDS", "999")
    assert latency._review_budget_seconds() == 45.0
