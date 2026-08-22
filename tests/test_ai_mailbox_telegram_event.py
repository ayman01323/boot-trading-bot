from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts import ai_mailbox_telegram_event as event


def test_claude_signal_is_persistent_agent(monkeypatch) -> None:
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: "CLAUDE_TO_GPT\nmessage_id: claude-1\nstatus: REQUEST\n\nhello\n")
    rows = event.resolve_signal("owner/repo", "Claude Mailbox Signal", "abc", "token")
    assert rows == [{"agent": "claude", "kind": "initiation", "message_id": "claude-1", "status": "REQUEST", "identity": "PERSISTENT_AGENT"}]


def test_claude_api_signal_is_never_labelled_persistent(monkeypatch) -> None:
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: (
        "CLAUDE_API_TO_GPT\nmessage_id: api-1\nstatus: RESPONSE\nin_reply_to: req-1\n"
        "identity: STATELESS_API_RESPONDER\n\nanswer\n"
    ))
    rows = event.resolve_signal("owner/repo", "Claude API Mailbox Signal", "abc", "token")
    assert rows[0]["agent"] == "claude"
    assert rows[0]["kind"] == "api_reply"
    assert rows[0]["message_id"] == "req-1"
    assert rows[0]["identity"] == "STATELESS_API_RESPONDER"


def test_gpt_to_claude_signal_creates_delivery_receipt(monkeypatch) -> None:
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: "GPT_TO_CLAUDE\nmessage_id: req-2\nstatus: REQUEST\n\nreview\n")
    rows = event.resolve_gpt_claude_delivery("owner/repo", "abc", "token")
    assert rows[0]["kind"] == "delivery"
    assert rows[0]["agent"] == "claude"
    assert rows[0]["message_id"] == "req-2"
    assert rows[0]["identity"] == "STATELESS_API_TARGET"


def test_gemini_agent_signal(monkeypatch) -> None:
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: "GEMINI_TO_GPT_INIT\nmessage_id: gemini-1\nstatus: REQUEST\n\nhello\n")
    rows = event.resolve_signal("owner/repo", "Gemini Mailbox Signal", "abc", "token")
    assert rows[0]["agent"] == "gemini"
    assert rows[0]["identity"] == "AGENT_MAILBOX"


def test_provider_signal_resolves_changed_outbound_mailboxes(monkeypatch) -> None:
    detail = {"files": [
        {"filename": ".github/ai-mailbox/gpt-to-deepseek.md"},
        {"filename": ".github/ai-mailbox/gpt-to-copilot.md"},
    ]}
    content = {
        ".github/ai-mailbox/gpt-to-deepseek.md": "GPT_TO_DEEPSEEK\nmessage_id: d-req\nstatus: REQUEST\n\nquestion\n",
        ".github/ai-mailbox/gpt-to-copilot.md": "GPT_TO_COPILOT\nmessage_id: c-req\nstatus: REQUEST\n\nquestion\n",
    }
    monkeypatch.setattr(event, "_github_json", lambda url, token: detail)
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: content[path])
    rows = event.resolve_provider_deliveries("owner/repo", "abc", "token")
    assert {(r["agent"], r["message_id"], r["kind"]) for r in rows} == {
        ("deepseek", "d-req", "delivery"), ("copilot", "c-req", "delivery")
    }
    assert all(r["identity"] == "STATELESS_API_TARGET" for r in rows)


def test_provider_relay_resolves_all_three_reply_agents(monkeypatch) -> None:
    commits = [{"sha": "c1"}, {"sha": "c2"}, {"sha": "c3"}]
    details = {
        "c1": {"commit": {"message": "Deepseek to GPT mailbox d1"}, "files": [{"filename": ".github/ai-mailbox/deepseek-to-gpt.md"}]},
        "c2": {"commit": {"message": "Gemini to GPT mailbox g1"}, "files": [{"filename": ".github/ai-mailbox/gemini-to-gpt.md"}]},
        "c3": {"commit": {"message": "Copilot to GPT mailbox c1"}, "files": [{"filename": ".github/ai-mailbox/copilot-to-gpt.md"}]},
    }
    def fake_json(url: str, token: str):
        if "/commits?" in url: return commits
        for sha, body in details.items():
            if url.endswith("/commits/" + sha): return body
        raise AssertionError(url)
    content = {
        ".github/ai-mailbox/deepseek-to-gpt.md": "DEEPSEEK_TO_GPT\nin_reply_to: d1\nstatus: COMPLETED\n\nanswer\n",
        ".github/ai-mailbox/gemini-to-gpt.md": "GEMINI_TO_GPT\nin_reply_to: g1\nstatus: COMPLETED\n\nanswer\n",
        ".github/ai-mailbox/copilot-to-gpt.md": "COPILOT_TO_GPT\nin_reply_to: c1\nstatus: BLOCKED\n\nanswer\n",
    }
    monkeypatch.setattr(event, "_github_json", fake_json)
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: content[path])
    rows = event.resolve_provider_replies("owner/repo", "2026-08-22T01:00:00Z", "2026-08-22T01:01:00Z", "token")
    assert {(r["agent"], r["message_id"], r["status"]) for r in rows} == {
        ("deepseek", "d1", "COMPLETED"), ("gemini", "g1", "COMPLETED"), ("copilot", "c1", "BLOCKED")
    }
    assert all(r["identity"] == "STATELESS_API_RESPONDER" for r in rows)


def test_provider_relay_ignores_non_workflow_run_event() -> None:
    assert event.resolve_events("owner/repo", "AI Mailbox Provider Relay", "abc", "2026-08-22T01:00:00Z", "2026-08-22T01:01:00Z", "push", "token") == []


def test_alert_workflow_is_event_only_and_covers_all_delivery_signals() -> None:
    path = Path(".github/workflows/ai-mailbox-telegram-alert.yml")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    trigger = workflow.get("on") or workflow.get(True)
    assert "schedule" not in trigger
    assert set(trigger["workflow_run"]["workflows"]) == {
        "Claude Mailbox Signal", "Gemini Mailbox Signal", "GPT Mailbox Signal",
        "Claude API Mailbox Signal", "AI Mailbox Provider Signal", "AI Mailbox Provider Relay",
    }
    text = path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" in text
    for secret in ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "COPILOT_ASSIGN_TOKEN", "ANTHROPIC_API_KEY"):
        assert secret not in text
    assert "/root/" not in text
    assert re.search(r"(?m)^\s*sudo\s+", text) is None
