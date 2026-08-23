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


def test_health_retry_dispatches_only_failed_or_missing_six_agent_reviewers() -> None:
    body = _text(".github/workflows/six-agent-health-retry.yml")
    assert "MISSING|INCOMPLETE|FAILED|ERROR|BLOCKED|BLOCKED_AUTH" in body
    for workflow in (
        "claude-fourth-engineering-agent.yml",
        "deepseek-fifth-engineering-agent.yml",
        "grok-sixth-engineering-agent.yml",
        "claude-fourth-strategy-agent.yml",
        "deepseek-fifth-strategy-agent.yml",
        "grok-sixth-strategy-agent.yml",
    ):
        assert workflow in body
    assert "-f source_commit=\"$source\"" in body
    assert "-f cycle_id=\"$cycle\"" in body
    assert "trade, deploy, restart, change LIVE/ARMED" in body
