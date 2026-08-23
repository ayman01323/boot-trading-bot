from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ai_agent_task_executor import (
    TaskError,
    build_task_envelope,
    execute_task,
    parse_task_envelope,
)
from scripts.ai_agent_ws_bus import Store


def test_task_envelope_round_trip() -> None:
    raw = build_task_envelope("read-file", {"path": "README.md"}, "inspect it")
    task = parse_task_envelope(raw)
    assert task is not None
    assert task["protocol"] == "ws-bus-v2"
    assert task["kind"] == "task"
    assert task["action"] == "READ_FILE"
    assert task["args"] == {"path": "README.md"}


def test_plain_message_is_not_task() -> None:
    assert parse_task_envelope("hello Claude") is None


def test_read_file_executes_without_model(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("safe evidence", encoding="utf-8")
    result = execute_task(
        {"action": "READ_FILE", "args": {"path": "notes.txt"}},
        root=tmp_path,
    )
    assert result["status"] == "COMPLETED"
    assert result["evidence"]["content"] == "safe evidence"


def test_sensitive_file_is_blocked(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=do-not-read", encoding="utf-8")
    result = execute_task(
        {"action": "READ_FILE", "args": {"path": ".env"}},
        root=tmp_path,
    )
    assert result["status"] == "FAILED"
    assert "safe task scope" in result["error"]


@pytest.mark.parametrize("action", ["WRITE_FILE", "MERGE", "DEPLOY", "TRADE", "SIGN"])
def test_protected_actions_are_never_executed(tmp_path: Path, action: str) -> None:
    result = execute_task({"action": action, "args": {}}, root=tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["protected"] is True


def test_invalid_task_args_rejected() -> None:
    raw = json.dumps({
        "protocol": "ws-bus-v2",
        "kind": "task",
        "action": "READ_FILE",
        "args": ["not", "an", "object"],
    })
    with pytest.raises(TaskError):
        parse_task_envelope(raw)


def test_store_tracks_task_progress_and_terminal_result(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "bus.sqlite3"))
    store.put("gpt-to-claude-1", "gpt", "claude", "{}")
    store.mark_delivered("gpt-to-claude-1")
    row = store.acknowledge("gpt-to-claude-1", "claude")
    assert row["status"] == "ACKNOWLEDGED"

    row = store.progress("gpt-to-claude-1", "claude", "ACCEPTED")
    assert row["status"] == "ACCEPTED"
    row = store.progress("gpt-to-claude-1", "claude", "EXECUTING")
    assert row["status"] == "EXECUTING"

    row = store.reply(
        "gpt-to-claude-1",
        "claude",
        '{"status":"COMPLETED"}',
        final_status="COMPLETED",
    )
    assert row["status"] == "COMPLETED"

    pending = store.pending_replies_for_sender("gpt")
    assert [item["message_id"] for item in pending] == ["gpt-to-claude-1"]


def test_ws_sender_direct_cli_imports_from_repo_root() -> None:
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "scripts/ai_agent_ws_send.py", "--help"],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "WebSocket bus" in proc.stdout
