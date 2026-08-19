from pathlib import Path


def test_master_ai_reports_menu_is_attached_and_complete():
    root = Path(__file__).resolve().parents[1]
    menu = (root / "learnerbot" / "telegram_ai_reports_menu_patch.py").read_text(encoding="utf-8")
    final_menu_layer = (root / "learnerbot" / "telegram_loss_alert_direction_patch.py").read_text(encoding="utf-8")

    assert '"🤖 AI Reports"' in menu
    assert '"menu:aiops"' in menu
    for callback in (
        "aiops:audit",
        "aiops:decision",
        "aiops:strategy",
        "aiops:updates",
    ):
        assert callback in menu

    # USERs must not receive the extra MASTER navigation row.
    assert "if not _is_master(app, chat_id):" in menu
    assert "return kb" in menu

    # Import after the compact/user-aware menu has been installed, so the
    # MASTER button cannot be lost behind an older menu wrapper.
    assert "from . import telegram_ai_reports_menu_patch" in final_menu_layer
