from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.ai_agent_ws_bus import Store
from scripts.ai_agent_ws_memory import recent_context
from scripts.ai_agent_ws_worker import build_prompt


def _reply(
    store: Store,
    message_id: str,
    sender: str,
    target: str,
    body: str,
    reply: str,
    *,
    thread_id: str = "",
    subject: str = "",
) -> None:
    store.put(message_id, sender, target, body, thread_id=thread_id, subject=subject)
    store.mark_delivered(message_id)
    store.acknowledge(message_id, target)
    store.reply(message_id, target, reply, final_status="REPLIED")


def test_recent_context_recovers_successful_agent_history(tmp_path: Path) -> None:
    db = tmp_path / "bus.sqlite3"
    store = Store(str(db))
    _reply(store, "m1", "gpt", "gemini", "What did I ask before?", "You asked about Strategy Factory.")
    _reply(store, "m2", "gemini", "gpt", "Please confirm receipt.", "Confirmed.")
    _reply(store, "m3", "gpt", "claude", "Claude only", "Not Gemini history")
    memory = recent_context("gemini", db_path=str(db), max_exchanges=6, max_chars=3200)
    assert "GPT → GEMINI: What did I ask before?" in memory
    assert "GEMINI → GPT: You asked about Strategy Factory." in memory
    assert "GEMINI → GPT: Please confirm receipt." in memory
    assert "GPT → GEMINI: Confirmed." in memory
    assert "Claude only" not in memory


def test_recent_context_excludes_current_message_id(tmp_path: Path) -> None:
    db = tmp_path / "bus.sqlite3"
    store = Store(str(db))
    _reply(store, "old", "gpt", "gemini", "Old question", "Old answer")
    _reply(store, "current", "gpt", "gemini", "Current question", "Current answer")
    memory = recent_context("gemini", current_message_id="current", db_path=str(db), max_exchanges=6, max_chars=3200)
    assert "Old question" in memory
    assert "Current question" not in memory


def test_recent_context_is_bounded_to_newest_history(tmp_path: Path) -> None:
    db = tmp_path / "bus.sqlite3"
    store = Store(str(db))
    for idx in range(1, 5):
        _reply(store, f"m{idx}", "gpt", "gemini", f"question-{idx}", f"answer-{idx}")
    memory = recent_context("gemini", db_path=str(db), max_exchanges=2, max_chars=3200)
    assert "question-1" not in memory
    assert "question-2" not in memory
    assert "question-3" in memory
    assert "question-4" in memory


def test_thread_memory_is_shared_across_agents_but_isolated_from_other_subjects(tmp_path: Path) -> None:
    db = tmp_path / "bus.sqlite3"
    store = Store(str(db))
    _reply(store, "risk-1", "master", "gpt", "Review HOOD risk", "Use liquidity checks", thread_id="thr-risk", subject="HOOD risk")
    _reply(store, "risk-2", "gpt", "claude", "Check the same risk", "Add holder concentration", thread_id="thr-risk", subject="HOOD risk")
    _reply(store, "infra-1", "master", "gemini", "Review server latency", "Measure p95", thread_id="thr-infra", subject="Infrastructure")

    memory = recent_context("gemini", thread_id="thr-risk", db_path=str(db), max_exchanges=6, max_chars=3200)
    assert "Review HOOD risk" in memory
    assert "Add holder concentration" in memory
    assert "Review server latency" not in memory
    assert "Measure p95" not in memory


def test_store_migrates_existing_database_without_losing_messages(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE messages (
            message_id TEXT PRIMARY KEY, sender TEXT NOT NULL, target TEXT NOT NULL,
            body TEXT NOT NULL, status TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            delivered_at INTEGER, acknowledged_at INTEGER, replied_at INTEGER, reply_delivered_at INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO messages(message_id,sender,target,body,status,created_at,updated_at) VALUES ('legacy','gpt','gemini','old','QUEUED',1,1)"
    )
    conn.commit()
    conn.close()

    store = Store(str(db))
    old = store.get("legacy")
    assert old is not None
    assert old["body"] == "old"
    assert old["thread_id"] == ""
    assert old["subject"] == ""
    store.put("threaded", "gpt", "gemini", "new", thread_id="thr-new", subject="New subject")
    new = store.get("threaded")
    assert new is not None
    assert new["thread_id"] == "thr-new"
    assert new["subject"] == "New subject"


def test_missing_memory_db_fails_open(tmp_path: Path) -> None:
    assert recent_context("gemini", db_path=str(tmp_path / "missing.sqlite3")) == ""


def test_worker_prompt_labels_thread_scope_and_external_chat_boundary() -> None:
    prompt = build_prompt(
        "gemini",
        {
            "from": "gpt",
            "message_id": "new",
            "thread_id": "thr-risk",
            "subject": "HOOD risk",
            "body": "What did we decide?",
        },
        "GPT → CLAUDE: Previous question\nCLAUDE → GPT: Previous answer",
    )
    assert "THIS SUBJECT THREAD" in prompt
    assert "Subject: HOOD risk" in prompt
    assert "Thread ID: thr-risk" in prompt
    assert "Previous question" in prompt
    assert "does NOT imply access to separate external web-chat sessions" in prompt
    assert "What did we decide?" in prompt
