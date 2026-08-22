from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT = ROOT / ".github/workflows/ai-bus-telegram-direct.yml"
BUS = ROOT / ".github/workflows/ai-agent-bus.yml"
LOADER = ROOT / "learnerbot/telegram_hi_keefek_patch.py"


def test_direct_telegram_workflow_is_event_driven_and_zero_idle_polling():
    text = DIRECT.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "Event-Driven AI Agent Bus" in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.event == 'issue_comment'" in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "TELEGRAM_MASTER_CHAT_ID" in text
    assert "api.telegram.org" in text
    assert "github/ai-agent-bus/latest.json" in text
    assert "telegram_sent=true" in text
    assert "schedule:" not in text
    assert "sleep(60" not in text
    assert "CHECK_SECONDS" not in text
    assert "OPENAI_API_KEY" not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "GEMINI_API_KEY" not in text
    assert "DEEPSEEK_API_KEY" not in text
    assert "COPILOT_ASSIGN_TOKEN" not in text


def test_bus_keeps_one_event_snapshot_for_direct_notification_and_audit():
    text = BUS.read_text(encoding="utf-8")
    assert "github/ai-agent-bus/latest.json" in text
    assert "schedule:" not in text


def test_production_does_not_load_sixty_second_agent_reply_poller():
    text = LOADER.read_text(encoding="utf-8")
    assert "from . import ai_bus_telegram_alert_patch" not in text
    assert "no 60-second ai-reviews poller is loaded here" in text
