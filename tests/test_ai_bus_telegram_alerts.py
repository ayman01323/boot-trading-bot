from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ai-agent-bus-telegram-snapshot.yml"
PATCH = ROOT / "learnerbot/ai_bus_telegram_alert_patch.py"
LOADER = ROOT / "learnerbot/telegram_hi_keefek_patch.py"


def test_snapshot_workflow_is_event_driven_and_zero_ai_calls():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "issue_comment:" in text
    assert "types: [created]" in text
    assert "issue.number == 333" in text
    assert "github-actions[bot]" in text
    assert "startsWith(github.event.comment.body, 'AI_BUS_REPLY')" in text
    assert "contents: write" in text
    assert "schedule:" not in text
    assert "OPENAI_API_KEY" not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "GEMINI_API_KEY" not in text
    assert "DEEPSEEK_API_KEY" not in text
    assert "COPILOT_ASSIGN_TOKEN" not in text
    assert "github/ai-agent-bus/latest.json" in text


def test_master_alert_patch_reuses_production_telegram_configuration():
    text = PATCH.read_text(encoding="utf-8")
    assert "master_chat_ids" in text
    assert "telegram_bot_token" in text
    assert "github/ai-agent-bus/latest.json" in text
    assert "disable_notification=False" in text
    assert "protect_content=True" in text
    assert ".ai_bus_telegram_alert_state.json" in text
    assert "MAX_EVENT_AGE_SECONDS" in text
    assert "last_message_id" in text
    assert "time.sleep(CHECK_SECONDS)" in text


def test_patch_is_loaded_by_production_telegram_chain():
    text = LOADER.read_text(encoding="utf-8")
    assert "from . import ai_bus_telegram_alert_patch" in text
