from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_central_factory_collects_deepseek_with_all_other_agents_and_gpt_master() -> None:
    central = _text("scripts/central_report_scheduler.py")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert "ops._panel_for = lambda package: list(AGENTS)" in central
    assert 'out["master"] = "gpt"' in central
    assert 'NON_GPT_REVIEWERS' in central


def test_deepseek_is_available_through_persistent_shared_bus() -> None:
    runtime = _text("learnerbot/ai_agent_ws_runtime_patch.py")
    worker = _text("scripts/ai_agent_ws_worker.py")
    provider = _text("learnerbot/ai_council_http_patch.py")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in runtime
    assert '"deepseek"' in worker
    assert "from learnerbot.ai_cost_provider_patch import call_provider" in worker
    # Provider credentials belong in the provider adapter/runtime-secret layer,
    # not in the transport worker. This keeps the WebSocket worker provider-agnostic.
    assert "DEEPSEEK_API_KEY" in provider


def test_deepseek_is_sanitised_control_option_and_telegram_visible() -> None:
    control = _text("learnerbot/ai_master_control.py")
    publisher = _text(".github/workflows/publish-ai-master-control.yml")
    ui = _text("learnerbot/telegram_five_agent_patch.py")
    assert '"deepseek"' in control
    assert "'deepseek'" in publisher
    assert '_PROVIDER_LABELS["deepseek"] = "DeepSeek"' in ui
    assert '"deepseek": "DeepSeek"' in ui
    assert 'aicfg:master:{lane}:deepseek' in ui
    assert "DEEPSEEK_API_KEY" not in control
