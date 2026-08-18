from types import SimpleNamespace

from learnerbot import profit_control_master_summary_patch as summary


class _Response:
    def __init__(self, ok=True):
        self._ok = ok

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": self._ok}


def _result(changed=True):
    return {
        "ok": True,
        "control_loop": {
            "generated_at": 1787090000,
            "previous_profile": "BASELINE",
            "active_profile": "PROFIT_FIRST" if changed else "BASELINE",
            "profile_changed": changed,
            "hour": {
                "closed_trades": 4,
                "wins": 3,
                "losses": 1,
                "net_sol": "0.0042",
                "profit_factor": "2.18",
            },
            "successful_leaders": 4,
            "blocked_leaders": 2,
            "ranking_error": "",
        },
    }


def test_active_master_ids_returns_every_active_master(monkeypatch):
    monkeypatch.setattr(summary, "all_users", lambda csv_dir, enabled_only=False: [
        {"telegram_id": "100", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "200", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "300", "role": "MASTER", "status": "INACTIVE"},
        {"telegram_id": "400", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "200", "role": "MASTER", "status": "ACTIVE"},
    ])
    app = SimpleNamespace(csv_dir="/tmp", telegram_chat_ids=[])
    assert summary._active_master_ids(app) == ["100", "200"]


def test_summary_reports_profile_update_and_profit_metrics():
    text = summary._summary_text(_result(True))
    assert "Entry policy UPDATED: BASELINE → PROFIT_FIRST" in text
    assert "Wins / losses: 3 / 1" in text
    assert "Realised net: +0.0042 SOL" in text
    assert "Profit factor: 2.18" in text
    assert "Hourly objective: PASS" in text
    assert "Successful leaders remembered: 4" in text
    assert "Leaders cooling down: 2" in text
    assert "LIVE/ARMED state" in text


def test_send_summary_posts_to_all_active_masters(monkeypatch):
    monkeypatch.setattr(summary, "all_users", lambda csv_dir, enabled_only=False: [
        {"telegram_id": "100", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "200", "role": "MASTER", "status": "ACTIVE"},
    ])
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response(True)

    monkeypatch.setattr(summary._worker.requests, "post", fake_post)
    app = SimpleNamespace(csv_dir="/tmp", telegram_bot_token="TOKEN", telegram_chat_ids=[])
    errors = summary.send_control_update_to_all_masters(app, _result(True))
    assert errors == []
    assert [c[1]["chat_id"] for c in calls] == ["100", "200"]
    assert all(c[1]["disable_notification"] is False for c in calls)


def test_delivery_failure_is_recorded_not_raised(monkeypatch):
    monkeypatch.setattr(summary, "all_users", lambda csv_dir, enabled_only=False: [
        {"telegram_id": "100", "role": "MASTER", "status": "ACTIVE"},
    ])

    def failing_post(url, json, timeout):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(summary._worker.requests, "post", failing_post)
    app = SimpleNamespace(csv_dir="/tmp", telegram_bot_token="TOKEN", telegram_chat_ids=[])
    errors = summary.send_control_update_to_all_masters(app, _result(False))
    assert len(errors) == 1
    assert errors[0]["telegram_id"] == "100"
    assert "telegram unavailable" in errors[0]["error"]
