from __future__ import annotations

from types import SimpleNamespace

from learnerbot import ai_agent_health_process_dedupe_patch as dedupe


def _snapshot(state: str = "NOT_WORKING") -> dict:
    return {
        "engineering": {
            "available": True,
            "cycle": "test-cycle",
            "agents": {
                "gpt": {"state": state, "reason": "test failure"},
            },
        },
        "strategy": {"available": False, "agents": {}},
    }


def test_same_health_warning_is_sent_once_across_fresh_state_reads(tmp_path, monkeypatch):
    app = SimpleNamespace(
        data_dir=tmp_path / "data",
        csv_dir=tmp_path / "CSVbot",
        telegram_bot_token="token",
    )
    app.csv_dir.mkdir(parents=True)
    sent = []
    monkeypatch.setattr(dedupe._health, "master_chat_ids", lambda _csv: ["5923828381"])
    monkeypatch.setattr(
        dedupe._tg,
        "send_to_chats",
        lambda token, chats, text, disable_notification=False: sent.append((token, tuple(chats), text)),
    )

    first = dedupe._notification_cycle(app, _snapshot(), now=1_000)
    second = dedupe._notification_cycle(app, _snapshot(), now=1_000)

    assert first == "WARNING"
    assert second == "NONE"
    assert len(sent) == 1


def test_warning_can_repeat_after_normal_30_minute_interval(tmp_path, monkeypatch):
    app = SimpleNamespace(
        data_dir=tmp_path / "data",
        csv_dir=tmp_path / "CSVbot",
        telegram_bot_token="token",
    )
    app.csv_dir.mkdir(parents=True)
    sent = []
    monkeypatch.setattr(dedupe._health, "master_chat_ids", lambda _csv: ["5923828381"])
    monkeypatch.setattr(dedupe._tg, "send_to_chats", lambda *args, **kwargs: sent.append(1))

    assert dedupe._notification_cycle(app, _snapshot(), now=1_000) == "WARNING"
    assert dedupe._notification_cycle(
        app,
        _snapshot(),
        now=1_000 + dedupe._health.WARNING_SECONDS,
    ) == "WARNING"
    assert len(sent) == 2


def test_recovery_is_sent_only_once(tmp_path, monkeypatch):
    app = SimpleNamespace(
        data_dir=tmp_path / "data",
        csv_dir=tmp_path / "CSVbot",
        telegram_bot_token="token",
    )
    app.csv_dir.mkdir(parents=True)
    sent = []
    monkeypatch.setattr(dedupe._health, "master_chat_ids", lambda _csv: ["5923828381"])
    monkeypatch.setattr(dedupe._tg, "send_to_chats", lambda *args, **kwargs: sent.append(1))

    dedupe._notification_cycle(app, _snapshot(), now=1_000)
    assert dedupe._notification_cycle(app, _snapshot("WORKING"), now=1_060) == "RECOVERY"
    assert dedupe._notification_cycle(app, _snapshot("WORKING"), now=1_120) == "NONE"
    assert len(sent) == 2


def test_process_lock_is_installed():
    assert getattr(dedupe._health, "_cross_process_dedupe_installed", False) is True
    assert dedupe._health._watch_loop is dedupe._watch_loop
