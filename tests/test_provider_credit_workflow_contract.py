from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_telegram_credit_patch_is_installed_before_final_runtime_invariant():
    text = (ROOT / "learnerbot" / "__main__.py").read_text(encoding="utf-8")
    provider_import = "from . import provider_credit_telegram_patch"
    invariant_import = "from . import trading_runtime_invariant_patch"
    assert provider_import in text
    assert text.index(provider_import) < text.index(invariant_import)


def test_workflow_uses_distinct_billing_credentials_and_no_telegram_secret():
    text = (ROOT / ".github" / "workflows" / "ai-provider-credit-alerts.yml").read_text(encoding="utf-8")
    assert "secrets.OPENAI_ADMIN_KEY" in text
    assert "secrets.COPILOT_BILLING_TOKEN" in text
    assert "vars.GEMINI_BUDGET_ID" in text
    assert "vars.GCP_WORKLOAD_IDENTITY_PROVIDER" in text
    assert "secrets.OPENAI_API_KEY" not in text
    assert "secrets.COPILOT_ASSIGN_TOKEN" not in text
    assert "TELEGRAM_BOT_TOKEN" not in text
    assert "TELEGRAM_MASTER_CHAT_ID" not in text


def test_gemini_notifications_are_acknowledged_only_after_status_publish():
    text = (ROOT / ".github" / "workflows" / "ai-provider-credit-alerts.yml").read_text(encoding="utf-8")
    assert text.index("Publish sanitised status to ai-reviews") < text.index(
        "Acknowledge published Gemini notifications"
    )


def test_workflow_fails_visibly_when_a_provider_monitor_is_unknown():
    text = (ROOT / ".github" / "workflows" / "ai-provider-credit-alerts.yml").read_text(encoding="utf-8")
    assert "Require all three monitors to be configured" in text
    assert "if row.get('state')=='UNKNOWN'" in text
