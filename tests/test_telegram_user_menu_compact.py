from types import SimpleNamespace

import pytest


def test_non_master_menu_is_short_and_user_only(monkeypatch, tmp_path):
    from learnerbot import telegram_user_menu_compact_patch as patch

    monkeypatch.setattr(patch, "is_master", lambda csv_dir, chat_id: False)
    app = SimpleNamespace(csv_dir=tmp_path)

    kb = patch.menu_keyboard(app, "461513364")
    rows = kb["inline_keyboard"]
    buttons = [button for row in rows for button in row]
    texts = [button["text"] for button in buttons]
    callbacks = {button["callback_data"] for button in buttons}

    assert len(rows) == 5
    assert len(buttons) == 9
    assert texts == [
        "🤖 SiBot", "💰 Capital",
        "🔐 Wallets", "💱 Trading",
        "⚡ Auto", "🛰 Opportunities",
        "⏰ Reports & Alerts",
        "📡 Status", "❓ Help",
    ]
    assert "menu:myalerts" in callbacks
    assert not any("All Chains" in text or "EVM + SOL" in text for text in texts)
    assert "menu:control" not in callbacks
    assert "menu:autodeploy" not in callbacks
    assert "menu:products" not in callbacks
    assert "menu:power" not in callbacks


def test_master_menu_is_preserved_and_gets_reports_entry(monkeypatch, tmp_path):
    from learnerbot import telegram_user_menu_compact_patch as patch

    previous = {"inline_keyboard": [[{"text": "MASTER", "callback_data": "menu:control"}]]}
    expected = {"inline_keyboard": [
        [{"text": "MASTER", "callback_data": "menu:control"}],
        [{"text": "⏰ My Reports & Loss Alerts", "callback_data": "menu:myalerts"}],
    ]}
    monkeypatch.setattr(patch, "is_master", lambda csv_dir, chat_id: True)
    monkeypatch.setattr(patch, "_PREV_MENU", lambda app=None, chat_id=None: previous)
    app = SimpleNamespace(csv_dir=tmp_path)

    assert patch.menu_keyboard(app, "5923828381") == expected


def _council_app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def test_ai_council_asks_all_five_agents_independently(tmp_path, monkeypatch):
    from learnerbot import ai_council

    calls = []

    def fake_call(provider, prompt):
        calls.append((provider, prompt))
        assert "one independent member of SiBot's AI Council" in prompt
        return 0, f"{provider} independent answer", ""

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _council_app(tmp_path)
    session = ai_council.create_session(app, 12345, "Question", mode="user")
    completed = ai_council.run_independent_answers(app, session["session_id"])

    assert set(completed["answers"]) == set(ai_council.PROVIDERS)
    assert {provider for provider, _ in calls} == set(ai_council.PROVIDERS)
    assert all(row["status"] == "DONE" for row in completed["answers"].values())


def test_ai_council_second_leader_uses_original_answers(tmp_path, monkeypatch):
    from learnerbot import ai_council

    leader_prompts = {}

    def fake_call(provider, prompt):
        if "one independent member of SiBot's AI Council" in prompt:
            return 0, f"ORIGINAL-{provider}-ANSWER", ""
        if "selected as SiBot AI Council Leader" in prompt:
            leader_prompts[provider] = prompt
            return 0, f"LEADER-{provider}-FINAL", ""
        raise AssertionError("unexpected prompt")

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _council_app(tmp_path)
    session = ai_council.create_session(app, 999, "Compare the evidence.", mode="master")
    ai_council.run_independent_answers(app, session["session_id"])
    ai_council.run_leader(app, session["session_id"], "gpt")
    ai_council.run_leader(app, session["session_id"], "claude")

    for provider in ai_council.PROVIDERS:
        assert f"ORIGINAL-{provider}-ANSWER" in leader_prompts["gpt"]
        assert f"ORIGINAL-{provider}-ANSWER" in leader_prompts["claude"]
    assert "LEADER-gpt-FINAL" not in leader_prompts["claude"]


def test_ai_council_failed_agent_does_not_block_others(tmp_path, monkeypatch):
    from learnerbot import ai_council

    def fake_call(provider, prompt):
        if provider == "copilot":
            return 90, "", "Copilot token unavailable"
        return 0, f"answer from {provider}", ""

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _council_app(tmp_path)
    session = ai_council.create_session(app, 321, "Question", mode="user")
    completed = ai_council.run_independent_answers(app, session["session_id"])

    assert completed["answers"]["copilot"]["status"] == "FAILED"
    assert sum(row["status"] == "DONE" for row in completed["answers"].values()) == 4


def test_ai_council_question_length_is_bounded(tmp_path):
    from learnerbot import ai_council

    app = _council_app(tmp_path)
    with pytest.raises(ai_council.CouncilError):
        ai_council.create_session(app, 1, "x" * (ai_council.MAX_QUESTION_CHARS + 1), mode="user")
