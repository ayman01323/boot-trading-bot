import json
from pathlib import Path

import pytest

from learnerbot import strategy_room as room


ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_PATCH = ROOT / "learnerbot" / "telegram_strategy_room_patch.py"
GPT_WORKFLOW = ROOT / ".github" / "workflows" / "gpt-strategy-room-controlled-ops.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-strategy-room-request.yml"


class FakeApp:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)


def test_gpt_leader_markers_are_parsed_and_hidden_from_user():
    visible, action, task = room.parse_gpt_leader_output(
        "Keep the current live thresholds unchanged. Add a shadow calibration metric.\n\n"
        "STRATEGY_ROOM_ACTION: DRAFT_SHADOW_CHANGE\n"
        "STRATEGY_ROOM_TASK: Add realised-vs-modelled edge calibration to the shadow evaluator.\n"
    )
    assert visible == "Keep the current live thresholds unchanged. Add a shadow calibration metric."
    assert action == "DRAFT_SHADOW_CHANGE"
    assert task == "Add realised-vs-modelled edge calibration to the shadow evaluator."
    assert "STRATEGY_ROOM_ACTION" not in visible


def test_protected_change_requires_human_approval_marker():
    prompt = room.build_gpt_leader_prompt(
        {
            "question": "Should we change the Solana stop loss?",
            "answers": {"gpt": {"status": "DONE", "answer": "Review it carefully."}},
        }
    )
    assert "stop-loss/take-profit" in prompt
    assert "HUMAN_APPROVAL_REQUIRED" in prompt
    assert "DRAFT-PR" not in prompt or "draft" in prompt.lower()
    assert "cannot merge or deploy" in prompt


def test_queue_requires_three_agents_and_writes_sanitised_bridge(tmp_path, monkeypatch):
    app = FakeApp(tmp_path / "data")
    bridge = tmp_path / "strategy_room_request.json"
    monkeypatch.setattr(room, "_bridge_path", lambda: bridge)

    with pytest.raises(ValueError, match="three completed agent reviews"):
        room.queue_draft_shadow_change(
            app,
            task="Add a shadow metric",
            question="Q",
            session_id="260822010101-abcd",
            requested_by="1",
            support_count=2,
        )

    value = room.queue_draft_shadow_change(
        app,
        task="Add a shadow metric\nwithout live changes",
        question="Check the strategy",
        session_id="260822010101-abcd",
        requested_by="1",
        support_count=3,
    )
    assert value["action"] == "draft_shadow_fix"
    assert value["support_count"] == 3
    assert value["draft_pr_only"] is True
    assert value["no_live_changes"] is True
    assert "\n" not in value["task"]
    persisted = json.loads(bridge.read_text(encoding="utf-8"))
    assert persisted["nonce"] == value["nonce"]


def test_strategy_room_health_uses_latest_mailbox_session(tmp_path):
    session_dir = tmp_path / "data" / "ai_council"
    session_dir.mkdir(parents=True)
    (session_dir / "260822010101-abcd.json").write_text(
        json.dumps(
            {
                "mode": "strategy_room",
                "created_epoch": 1000,
                "updated_epoch": 1000,
                "answers": {
                    "gpt": {"status": "DONE", "answer": "ok"},
                    "claude": {"status": "FAILED", "error": "provider timeout"},
                    "gemini": {"status": "DONE", "answer": "ok"},
                },
            }
        ),
        encoding="utf-8",
    )
    health = room.strategy_room_agent_health(tmp_path, now=1010, max_age_seconds=60)
    assert health["agents"]["gpt"]["state"] == "WORKING"
    assert health["agents"]["gemini"]["state"] == "WORKING"
    assert health["agents"]["claude"]["state"] == "FAILED"
    assert "timeout" in health["agents"]["claude"]["reason"]
    assert health["agents"]["copilot"]["state"] == "WAITING"


def test_telegram_strategy_room_is_master_only_and_uses_all_agents_then_gpt():
    text = TELEGRAM_PATCH.read_text(encoding="utf-8")
    assert "🧠 Strategy Room" in text
    assert 'callback_data": "sr:ask"' in text
    assert 'mode="strategy_room"' in text
    assert "run_independent_answers" in text
    assert 'call_provider("gpt"' in text
    assert "queue_draft_shadow_change" in text
    assert "HUMAN_APPROVAL_REQUIRED" in text
    assert "if not _master(app, cb_tid)" in text


def test_gpt_worker_is_draft_only_and_rejects_protected_paths():
    text = GPT_WORKFLOW.read_text(encoding="utf-8")
    assert "strategy_auto_path_allowed" in text
    assert "--sandbox workspace-write" in text
    assert "gh pr create" in text
    assert "--draft" in text
    assert "gh pr merge" not in text
    assert "deploy" not in "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith(("gh ", "python", "codex"))
    ).lower()
    assert "CSVbot or runtime configuration" in text
    assert "stop-loss/take-profit" in text
    assert "wallets, signing, private keys" in text


def test_publisher_is_zero_provider_call_when_nonce_is_unchanged():
    text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "cron: '*/5 * * * *'" in text
    assert "zero provider calls" in text.lower()
    assert "support_count'] < 3" in text
    assert "gpt-strategy-room-controlled-ops.yml" in text
    assert "OPENAI_API_KEY" not in text
    assert "codex" not in text.lower()
