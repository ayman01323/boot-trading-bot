import json
import os

from learnerbot import telegram_ai_health_truth_patch as truth
from learnerbot import kimi_ai_health_roster_patch as kimi_health


def _health(states, **extra):
    value = {
        "agents": {
            provider: {"state": state, "reason": reason}
            for provider, (state, reason) in states.items()
        }
    }
    value.update(extra)
    return value


def _ensure_kimi_roster():
    kimi_health.install()


def test_connected_workers_override_stale_review_failures(monkeypatch):
    _ensure_kimi_roster()
    monkeypatch.setattr(
        truth,
        "_runtime_connections",
        lambda: {
            "available": True,
            "connected_agents": set(truth._compact.PROVIDERS),
            "updated_epoch": 1,
        },
    )
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": True,
            "_truth_age_seconds": 50 * 60,
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )

    broken = {
        provider: ("NOT_WORKING", "old pipeline failure")
        for provider in truth._compact.PROVIDERS
    }
    engineering = _health(broken)
    strategy = _health(broken)

    text = truth.provider_health_text(engineering, strategy)

    assert "🟢 <b>7 healthy</b> · 0 need verification · 0 problems" in text
    assert "🟢 GPT — Worker connected · API last OK 50m ago" in text
    assert "🟢 Claude — Worker connected · API last OK 50m ago" in text
    assert "🟢 Gemini — Worker connected" in text
    assert "🟢 DeepSeek — Worker connected · API last OK 50m ago" in text
    assert "🟢 Grok — Worker connected · API last OK 50m ago" in text
    assert "🟢 Kimi — Worker connected" in text
    assert "🟢 Copilot — Worker connected" in text
    assert "Agent problem" not in text


def test_fresh_provider_failure_still_beats_connected_worker(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_runtime_connections",
        lambda: {"available": True, "connected_agents": {"gpt"}, "updated_epoch": 1},
    )
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": False,
            "_truth_age_seconds": 20,
            "openai": {"state": "FAILED"},
        },
    )

    icon, status = truth._provider_status("gpt", {}, {}, truth._fresh_preflight(), truth._runtime_connections())
    assert icon == "🔴"
    assert status == "Worker connected · API/provider problem"


def test_runtime_connection_file_is_ignored_outside_production_run(tmp_path, monkeypatch):
    _ensure_kimi_roster()
    status = tmp_path / "connections.json"
    status.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "connected_agents": list(truth._compact.PROVIDERS),
                "updated_epoch": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(truth, "_CONNECTION_STATUS_PATH", status)
    monkeypatch.setattr(truth.sys, "argv", ["pytest", "tests/test_ai_health_runtime_truth.py"])

    assert truth._runtime_connections() == {}


def test_explicit_strategy_snapshot_does_not_borrow_persisted_source_commit(monkeypatch):
    health = _health({"gpt": ("WORKING", "HEALTHY")})
    monkeypatch.setattr(truth, "_current_checkout_sha", lambda: "new-head")
    monkeypatch.setattr(
        truth._warning,
        "read_json",
        lambda root, path: {"source_commit": "old-head"} if path == "strategy/latest_status.json" else {},
    )

    assert truth._review_stale_reason("strategy", health) == ""


def test_stale_review_snapshot_is_not_presented_as_current_failure(monkeypatch):
    _ensure_kimi_roster()
    health = _health(
        {
            "gpt": ("WORKING", "HEALTHY"),
            "claude": ("NOT_WORKING", "pipeline failed"),
            "gemini": ("NOT_WORKING", "pipeline failed"),
            "deepseek": ("NOT_WORKING", "model config"),
            "grok": ("NOT_WORKING", "pipeline failed"),
            "kimi": ("NOT_WORKING", "pipeline failed"),
            "copilot": ("NOT_WORKING", "pipeline failed"),
        },
        source_commit="old",
        age_seconds=10_000,
    )
    monkeypatch.setattr(truth, "_review_stale_reason", lambda lane, value: "Review snapshot predates current code")

    text = truth.lane_summary_text("engineering", health)

    assert "🟡 <b>Review snapshot predates current code · refresh needed</b>" in text
    assert "Pipeline failure" not in text
    assert "Model config" not in text
    assert "1 working · 0 in progress · 6 issues" not in text


def test_factory_waiting_without_request_is_idle():
    _ensure_kimi_roster()
    factory = _health(
        {
            provider: ("WAITING", "No Strategy Room request recorded")
            for provider in truth._compact.PROVIDERS
        }
    )

    text = truth.factory_summary_text(factory)

    assert "⚪ <b>Idle · no active factory request</b>" in text
    assert "0 working · 7 in progress" not in text
