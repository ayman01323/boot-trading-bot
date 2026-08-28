from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_grok_commands_are_isolated_and_master_only():
    text = _text("learnerbot/telegram_grok_known_assets_control_patch.py")
    assert '"/grokstatus"' in text
    assert '"/grokarm"' in text
    assert '"/grokstop"' in text
    assert "is_master(app.csv_dir, tid)" in text
    assert "PAPER_ONLY" in text
    assert '"live_money_enabled": False' in text
    assert "no signing and no transaction broadcast" in text
    for forbidden in (
        "/claude_arm_live",
        "/sibot1solarm",
        "SolanaLiveExecutor",
        "sendRawTransaction",
        "sign_transaction",
        "private_key",
    ):
        assert forbidden not in text


def test_grok_commands_are_registered_after_existing_ai_controls():
    text = _text("learnerbot/telegram_command_scope_patch.py")
    assert "telegram_deepseek_control_patch" in text
    assert "telegram_grok_known_assets_control_patch" in text
    assert text.index("telegram_deepseek_control_patch") < text.index("telegram_grok_known_assets_control_patch")
    assert '"grokstatus"' in text
    assert '"grokarm"' in text
    assert '"grokstop"' in text


def test_claude_prechain_does_not_import_grok_control():
    text = _text("claude-trading-bot/claude_bot_patches.py")
    assert "grok_telegram_control_patch" not in text
    assert "telegram_control_patch.install()" in text
    assert "claude_runtime_health_patch.install()" in text
