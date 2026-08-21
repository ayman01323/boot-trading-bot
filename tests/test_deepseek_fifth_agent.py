from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deepseek_strategy_workflow_is_independent_report_only_and_plan_mode() -> None:
    text = _text(".github/workflows/deepseek-fifth-strategy-agent.yml")
    assert "DeepSeek Fifth Strategy Agent" in text
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "https://api.deepseek.com/anthropic" in text
    assert "deepseek-v4-flash" in text
    assert "--permission-mode plan" in text
    assert '"provider":"deepseek"' in text
    assert '"scope":"MULTI_AGENT_STRATEGY_REVIEW"' in text
    assert '"review_only":true' in text
    assert '"no_live_changes":true' in text
    assert "strategy/deepseek_cost_gate_state.json" in text
    assert 'deepseek.json' in text and 'deepseek.md' in text
    assert "DeepSeek reviewer changed tracked/out-of-scope files" in text
    for forbidden in ("sudo ", "deploy-boot-trading-bot", "PRIVATE_KEY", "mnemonic", "seed phrase"):
        assert forbidden not in text


def test_deepseek_engineering_workflow_audits_cost_bandwidth_disk_without_mutation() -> None:
    text = _text(".github/workflows/deepseek-fifth-engineering-agent.yml")
    assert "DeepSeek Fifth Engineering Agent" in text
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "https://api.deepseek.com/anthropic" in text
    assert "deepseek-v4-flash" in text
    assert "--permission-mode plan" in text
    assert '"provider":"deepseek"' in text
    assert '"scope":"FULL_REPOSITORY_BUG_AUDIT"' in text
    assert "API/model cost" in text
    assert "Server bandwidth" in text
    assert "Disk usage" in text
    assert "operational_efficiency" in text
    assert '"report_only":true' in text
    assert '"no_live_changes":true' in text
    for forbidden in ("sudo ", "deploy-boot-trading-bot", "PRIVATE_KEY", "mnemonic", "seed phrase"):
        assert forbidden not in text


def test_selected_master_collects_and_can_fallback_to_deepseek() -> None:
    workflow = _text(".github/workflows/selected-ai-master.yml")
    runner = _text("scripts/resilient_selected_master_v2.py")
    assert '"DeepSeek Fifth Strategy Agent"' in workflow
    assert '"DeepSeek Fifth Engineering Agent"' in workflow
    assert "gpt gemini claude deepseek" in workflow
    assert "secrets.DEEPSEEK_API_KEY" in workflow
    assert "'auto','gpt','claude','gemini','deepseek','copilot'" in workflow
    assert '_FALLBACK = ("gpt", "claude", "gemini", "deepseek", "copilot")' in runner
    assert 'DEEPSEEK_API_KEY' in runner
    assert 'ANTHROPIC_AUTH_TOKEN' in runner
    assert 'env.pop("ANTHROPIC_API_KEY", None)' in runner
    assert 'failed_agent_count"] = max(0, 5 - len(valid_reports))' in runner


def test_deepseek_is_sanitised_control_option_and_telegram_visible() -> None:
    control = _text("learnerbot/ai_master_control.py")
    publisher = _text(".github/workflows/publish-ai-master-control.yml")
    ui = _text("learnerbot/telegram_five_agent_patch.py")
    assert '"deepseek"' in control
    assert "'deepseek'" in publisher
    assert '_PROVIDER_LABELS["deepseek"] = "DeepSeek"' in ui
    assert 'aicfg:master:{lane}:deepseek' in ui
    assert "five_agent_reports_complete" in ui
    # The current compact five-agent UI composes the label and DONE icon dynamically:
    # "DeepSeek — ✅ Completed" rather than embedding the old literal "DeepSeek ✅".
    assert '"deepseek": "DeepSeek"' in ui
    assert 'return "✅"' in ui
    assert 'f"• {label} — {_icon(value)}' in ui
    # Sanitised control never carries provider credentials.
    assert "DEEPSEEK_API_KEY" not in control


def test_deepseek_patch_is_installed_after_legacy_four_agent_presentation() -> None:
    scope = _text("learnerbot/telegram_command_scope_patch.py")
    assert "telegram_four_agent_strategy_patch" in scope
    assert "telegram_five_agent_patch" in scope
    assert scope.index("telegram_four_agent_strategy_patch") < scope.index("telegram_five_agent_patch")
