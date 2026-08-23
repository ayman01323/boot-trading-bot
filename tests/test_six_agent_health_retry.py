from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deepseek_claude_code_uses_recognised_alias_mapped_to_v4_flash() -> None:
    for path in (
        ".github/workflows/deepseek-fifth-engineering-agent.yml",
        ".github/workflows/deepseek-fifth-strategy-agent.yml",
    ):
        body = _text(path)
        assert "https://api.deepseek.com/anthropic" in body
        assert "DEEPSEEK_CLAUDE_MODEL: 'claude-sonnet-4-5'" in body
        assert 'export ANTHROPIC_MODEL="$DEEPSEEK_CLAUDE_MODEL"' in body
        assert "CLAUDE_CODE_EFFORT_LEVEL: 'max'" in body
        assert "deepseek-v4-flash" in body  # documented server-side mapping target
        assert 'export ANTHROPIC_MODEL="$DEEPSEEK_ENGINEERING_MODEL"' not in body
        assert 'export ANTHROPIC_MODEL="$DEEPSEEK_STRATEGY_MODEL"' not in body


def test_legacy_six_agent_retry_delegates_to_authoritative_seven_agent_watch() -> None:
    legacy = _text(".github/workflows/six-agent-health-retry.yml")
    watch = _text(".github/workflows/ai-agent-recovery.yml")

    assert "schedule:" not in legacy
    assert "ai-agent-recovery.yml" in legacy
    assert "repair=true" in legacy
    assert "seven-agent recovery watch is authoritative" in legacy

    assert "MISSING|INCOMPLETE|FAILED|ERROR|BLOCKED|BLOCKED_AUTH" in watch
    assert "providers=(gpt claude gemini deepseek grok kimi copilot)" in watch
    for workflow in (
        "engineering-agent-retry.yml",
        "claude-fourth-engineering-agent.yml",
        "deepseek-fifth-engineering-agent.yml",
        "grok-sixth-engineering-agent.yml",
        "kimi-seventh-engineering-agent.yml",
        "engineering-copilot-assignment-reconciler.yml",
        "claude-exact-strategy-retry.yml",
        "deepseek-fifth-strategy-agent.yml",
        "grok-sixth-strategy-agent.yml",
        "kimi-seventh-strategy-agent.yml",
        "strategy-copilot-assignment-reconciler.yml",
    ):
        assert workflow in watch
    assert "Scheduled pass is read-only" in watch
    assert "Trading/LIVE/capital/wallet/signing authority: none." in watch
