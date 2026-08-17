from learnerbot import telegram_visual_ui_patch as visual


def test_compact_text_adds_visual_divider_and_replaces_bullets():
    text = "<b>📡 STATUS</b>\n\n• BSC — OK\n• BASE — OK"
    out = visual._compact_text(text, 20)
    assert "━━━━━━━━━━━━━━━━━━━━" in out
    assert "▫️ BSC" in out
    assert "▫️ BASE" in out


def test_compact_text_limits_long_legacy_pages():
    lines = ["<b>📊 LONG REPORT</b>"] + [f"item {i}: value" for i in range(80)]
    out = visual._compact_text("\n".join(lines), 18)
    assert len(out.splitlines()) <= 21
    assert "Main view simplified" in out


def test_main_menu_is_grouped_and_keeps_core_callbacks():
    kb = visual.menu_keyboard()
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b.get("callback_data") for b in buttons}
    assert "menu:sibot" in callbacks
    assert "menu:capital" in callbacks
    assert "menu:wallet" in callbacks
    assert "menu:trading" in callbacks
    assert "menu:auto" in callbacks
    assert "menu:opportunities" in callbacks
    assert "menu:status" in callbacks
    assert len(kb["inline_keyboard"]) == 5


def test_home_page_is_short_visual_card():
    out = visual.home_text()
    assert "BOOT Trading Dashboard" in out
    assert "QUICK ACCESS" in out
    assert "SiBot" in out
    assert len(out.splitlines()) <= 15


def test_visual_patch_loaded_last():
    from pathlib import Path
    main = (Path(__file__).resolve().parents[1] / "learnerbot" / "__main__.py").read_text()
    assert main.index("telegram_pending_command_patch") < main.index("telegram_visual_ui_patch")
