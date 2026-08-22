from __future__ import annotations

from pathlib import Path

import yaml

from scripts import ai_mailbox_telegram_event as event


def test_claude_signal_resolves_exact_message(monkeypatch) -> None:
    monkeypatch.setattr(
        event,
        "_fetch_content",
        lambda repo, path, ref, token: "CLAUDE_TO_GPT\nmessage_id: claude-1\nstatus: REQUEST\n\nhello\n",
    )
    rows = event.resolve_signal("owner/repo", "Claude Mailbox Signal", "abc", "token")
    assert rows == [{"agent": "claude", "kind": "initiation", "message_id": "claude-1", "status": "REQUEST"}]


def test_gemini_signal_resolves_exact_message(monkeypatch) -> None:
    monkeypatch.setattr(
        event,
        "_fetch_content",
        lambda repo, path, ref, token: "GEMINI_TO_GPT_INIT\nmessage_id: gemini-1\nstatus: REQUEST\n\nhello\n",
    )
    rows = event.resolve_signal("owner/repo", "Gemini Mailbox Signal", "abc", "token")
    assert rows[0]["agent"] == "gemini"
    assert rows[0]["kind"] == "initiation"
    assert rows[0]["message_id"] == "gemini-1"


def test_provider_relay_resolves_all_three_reply_agents(monkeypatch) -> None:
    commits = [{"sha": "c1"}, {"sha": "c2"}, {"sha": "c3"}]
    details = {
        "c1": {
            "commit": {"message": "Deepseek to GPT mailbox d1"},
            "files": [{"filename": ".github/ai-mailbox/deepseek-to-gpt.md"}],
        },
        "c2": {
            "commit": {"message": "Gemini to GPT mailbox g1"},
            "files": [{"filename": ".github/ai-mailbox/gemini-to-gpt.md"}],
        },
        "c3": {
            "commit": {"message": "Copilot to GPT mailbox c1"},
            "files": [{"filename": ".github/ai-mailbox/copilot-to-gpt.md"}],
        },
    }

    def fake_json(url: str, token: str):
        if "/commits?" in url:
            return commits
        for sha, body in details.items():
            if url.endswith("/commits/" + sha):
                return body
        raise AssertionError(url)

    content = {
        ".github/ai-mailbox/deepseek-to-gpt.md": "DEEPSEEK_TO_GPT\nin_reply_to: d1\nstatus: COMPLETED\n\nanswer\n",
        ".github/ai-mailbox/gemini-to-gpt.md": "GEMINI_TO_GPT\nin_reply_to: g1\nstatus: COMPLETED\n\nanswer\n",
        ".github/ai-mailbox/copilot-to-gpt.md": "COPILOT_TO_GPT\nin_reply_to: c1\nstatus: BLOCKED\n\nanswer\n",
    }
    monkeypatch.setattr(event, "_github_json", fake_json)
    monkeypatch.setattr(event, "_fetch_content", lambda repo, path, ref, token: content[path])
    rows = event.resolve_provider_replies(
        "owner/repo",
        "2026-08-22T01:00:00Z",
        "2026-08-22T01:01:00Z",
        "token",
    )
    assert {(r["agent"], r["message_id"], r["status"]) for r in rows} == {
        ("deepseek", "d1", "COMPLETED"),
        ("gemini", "g1", "COMPLETED"),
        ("copilot", "c1", "BLOCKED"),
    }


def test_provider_relay_ignores_unrelated_commit_touching_reply_path(monkeypatch) -> None:
    monkeypatch.setattr(event, "_github_json", lambda url, token: (
        [{"sha": "x1"}]
        if "/commits?" in url
        else {
            "commit": {"message": "Unrelated mailbox maintenance"},
            "files": [{"filename": ".github/ai-mailbox/gemini-to-gpt.md"}],
        }
    ))
    monkeypatch.setattr(
        event,
        "_fetch_content",
        lambda repo, path, ref, token: "GEMINI_TO_GPT\nin_reply_to: old\nstatus: COMPLETED\n\nold\n",
    )
    rows = event.resolve_provider_replies(
        "owner/repo",
        "2026-08-22T01:00:00Z",
        "2026-08-22T01:01:00Z",
        "token",
    )
    assert rows == []


def test_provider_relay_ignores_non_workflow_run_event() -> None:
    rows = event.resolve_events(
        "owner/repo",
        "AI Mailbox Provider Relay",
        "abc",
        "2026-08-22T01:00:00Z",
        "2026-08-22T01:01:00Z",
        "push",
        "token",
    )
    assert rows == []


def test_alert_workflow_is_event_only_and_has_no_ai_credentials() -> None:
    path = Path(".github/workflows/ai-mailbox-telegram-alert.yml")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    trigger = workflow.get("on") or workflow.get(True)
    assert "schedule" not in trigger
    assert set(trigger["workflow_run"]["workflows"]) == {
        "Claude Mailbox Signal",
        "Gemini Mailbox Signal",
        "AI Mailbox Provider Relay",
    }
    job = workflow["jobs"]["notify"]
    assert job["runs-on"] == "ubuntu-latest"
    text = path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "OPENAI_API_KEY" not in text
    assert "GEMINI_API_KEY" not in text
    assert "DEEPSEEK_API_KEY" not in text
    assert "COPILOT_ASSIGN_TOKEN" not in text
    assert "/root/" not in text
    assert "sudo " not in text
