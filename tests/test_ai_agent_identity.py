from pathlib import Path

import pytest

from learnerbot.ai_agent_identity import AGENT_IDENTITIES, agent_label, github_header, pr_prefix


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("provider", "label", "prefix"),
    [
        ("gpt", "🟢 GPT", "[GPT]"),
        ("gemini", "🔵 GEMINI", "[GEMINI]"),
        ("copilot", "🟡 COPILOT", "[COPILOT]"),
        ("claude", "🟣 CLAUDE", "[CLAUDE]"),
    ],
)
def test_provider_identity_is_unambiguous(provider, label, prefix):
    assert agent_label(provider) == label
    assert pr_prefix(provider) == prefix
    assert f"**AGENT: {label.split(' ', 1)[1]}**" in github_header(provider)


def test_unknown_provider_cannot_impersonate_supported_agent():
    with pytest.raises(ValueError):
        agent_label("unknown")


def test_all_provider_instruction_surfaces_require_visible_identity():
    expected = {
        "AGENTS.md": "🟢 AGENT: GPT",
        "GEMINI.md": "🔵 AGENT: GEMINI",
        "CLAUDE.md": "🟣 AGENT: CLAUDE",
        ".github/copilot-instructions.md": "🟡 AGENT: COPILOT",
    }
    for relative, marker in expected.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "docs/AI_AGENT_IDENTITY.md" in text
        assert marker in text
        assert "AI-Agent:" in text


def test_identity_document_covers_comments_prs_reports_and_telegram():
    text = (ROOT / "docs/AI_AGENT_IDENTITY.md").read_text(encoding="utf-8")
    for word in ("comment", "PR body", "report", "Telegram"):
        assert word in text
    for row in AGENT_IDENTITIES.values():
        assert row["name"] in text
        assert row["pr_prefix"] in text


def test_four_agent_telegram_renderer_uses_shared_provider_labels():
    text = (ROOT / "learnerbot/telegram_four_agent_strategy_patch.py").read_text(encoding="utf-8")
    assert "from .ai_agent_identity import agent_label" in text
    assert "agent_label(name)" in text
    for provider in AGENT_IDENTITIES:
        assert provider in text
