from __future__ import annotations

from pathlib import Path

from scripts import ai_agent_bus
from scripts import ai_agent_bus_provider_compat

ROOT = Path(__file__).resolve().parents[1]


def test_event_bus_all_from_gpt_targets_all_six_other_agents() -> None:
    ai_agent_bus_provider_compat.install()
    assert ai_agent_bus.AGENTS == (
        "gpt",
        "claude",
        "gemini",
        "deepseek",
        "copilot",
        "grok",
        "kimi",
    )
    envelope = ai_agent_bus.parse_envelope(
        "AI_BUS\n"
        "message_id: all-six-test\n"
        "from: GPT\n"
        "to: ALL\n"
        "mode: DIRECT\n"
        "max_hops: 1\n\n"
        "Design one independent Basic Engine strategy."
    )
    targets = [agent for agent in ai_agent_bus.AGENTS if agent != envelope.sender]
    assert targets == ["claude", "gemini", "deepseek", "copilot", "grok", "kimi"]


def test_event_bus_workflow_supplies_kimi_credentials_and_reports_kimi() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-agent-bus.yml").read_text(encoding="utf-8")
    assert "KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}" in workflow
    assert "MOONSHOT_API_KEY: ${{ secrets.MOONSHOT_API_KEY }}" in workflow
    assert "KIMI_COUNCIL_MODEL: ${{ vars.KIMI_COUNCIL_MODEL || 'kimi-k2.6' }}" in workflow
    assert "COPILOT|GROK|KIMI" in workflow
