from __future__ import annotations

from pathlib import Path

from learnerbot import ai_council


ROOT = Path(__file__).resolve().parents[1]


def test_ai_council_marks_v4_flash_as_one_million_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, prompt, env, *, stdin=False):
        captured["cmd"] = cmd
        captured["env"] = env
        return 0, "ok", ""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MASTER_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(ai_council, "_run", fake_run)

    rc, out, err = ai_council.call_provider("deepseek", "test")

    assert (rc, out, err) == (0, "ok", "")
    env = captured["env"]
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash[1m]"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "deepseek-v4-flash[1m]"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "deepseek-v4-flash[1m]"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-v4-flash[1m]"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4-flash[1m]"


def test_ai_council_does_not_claim_one_million_context_for_other_models(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, prompt, env, *, stdin=False):
        captured["env"] = env
        return 0, "ok", ""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MASTER_MODEL", "deepseek-chat")
    monkeypatch.setattr(ai_council, "_run", fake_run)

    ai_council.call_provider("deepseek", "test")

    assert captured["env"]["ANTHROPIC_MODEL"] == "deepseek-chat"


def test_selected_master_and_agent_workflows_use_v4_flash_1m_annotation() -> None:
    selected = (ROOT / "scripts/resilient_selected_master_v2.py").read_text(encoding="utf-8")
    assert 'if model == "deepseek-v4-flash":' in selected
    assert 'model = f"{model}[1m]"' in selected

    strategy = (ROOT / ".github/workflows/deepseek-fifth-strategy-agent.yml").read_text(encoding="utf-8")
    assert "deepseek-v4-flash[1m]" in strategy
    assert 'if [[ "$DEEPSEEK_STRATEGY_MODEL" == "deepseek-v4-flash" ]]' in strategy

    engineering = (ROOT / ".github/workflows/deepseek-fifth-engineering-agent.yml").read_text(encoding="utf-8")
    assert "deepseek-v4-flash[1m]" in engineering
    assert 'if [[ "$DEEPSEEK_ENGINEERING_MODEL" == "deepseek-v4-flash" ]]' in engineering
