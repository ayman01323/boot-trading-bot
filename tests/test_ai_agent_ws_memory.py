from __future__ import annotations

from pathlib import Path

from scripts.ai_agent_ws_bus import Store
from scripts.ai_agent_ws_memory import recent_context
from scripts.ai_agent_ws_worker import build_prompt


def _reply(store: Store, message_id: str, sender: str, target: str, body: str, reply: str) -> None:
    store.put(message_id, sender, target, body)
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

    memory = recent_context(
        "gemini",
        current_message_id="current",
        db_path=str(db),
        max_exchanges=6,
        max_chars=3200,
    )
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


def test_missing_memory_db_fails_open(tmp_path: Path) -> None:
    assert recent_context("gemini", db_path=str(tmp_path / "missing.sqlite3")) == ""


def test_worker_prompt_labels_memory_scope_and_external_chat_boundary() -> None:
    prompt = build_prompt(
        "gemini",
        {"from": "gpt", "message_id": "new", "body": "What did I ask last time?"},
        "GPT → GEMINI: Previous question\nGEMINI → GPT: Previous answer",
    )
    assert "RECENT STRATEGY FACTORY CONVERSATION MEMORY" in prompt
    assert "Previous question" in prompt
    assert "does NOT imply access to separate external web-chat sessions" in prompt
    assert "What did I ask last time?" in prompt
