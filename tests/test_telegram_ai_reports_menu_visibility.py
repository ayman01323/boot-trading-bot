from pathlib import Path


def test_master_ai_reports_menu_is_attached_and_complete():
    root = Path(__file__).resolve().parents[1]
    menu = (root / "learnerbot" / "telegram_ai_reports_menu_patch.py").read_text(encoding="utf-8")
    final_menu_layer = (root / "learnerbot" / "telegram_loss_alert_direction_patch.py").read_text(encoding="utf-8")

    assert '"🤖 AI Reports & Control"' in menu
    assert '"menu:aiops"' in menu
    for callback in (
        "aiops:control",
        "aiops:audit",
        "aiops:decision",
        "aiops:strategy",
        "aiops:updates",
        "aicfg:run:strategy",
        "aicfg:run:engineering",
        "aicfg:run:both",
    ):
        assert callback in menu

    for provider in ("auto", "gpt", "claude", "gemini", "copilot"):
        assert f'mbtn("strategy", "{provider}")' in menu
        assert f'mbtn("engineering", "{provider}")' in menu

    # USERs must not receive the extra MASTER navigation/control row.
    assert "if not _is_master(app, chat_id):" in menu
    assert "return kb" in menu
    assert '"MASTER only"' in menu

    # Import after the compact/user-aware menu has been installed, so the
    # MASTER button cannot be lost behind an older menu wrapper.
    assert "from . import telegram_ai_reports_menu_patch" in final_menu_layer
