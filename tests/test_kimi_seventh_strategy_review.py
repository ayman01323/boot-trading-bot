from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_central_factory_is_seven_agent_and_includes_kimi() -> None:
    central = (ROOT / "scripts" / "central_report_scheduler.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert "ops._panel_for = lambda package: list(AGENTS)" in central
    assert 'out["master"] = "gpt"' in central


def test_kimi_is_available_through_persistent_shared_bus() -> None:
    runtime = (ROOT / "learnerbot" / "ai_agent_ws_runtime_patch.py").read_text(encoding="utf-8")
    worker = (ROOT / "scripts" / "ai_agent_ws_worker.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in runtime
    assert '"kimi"' in worker
    assert "KIMI_API_KEY" in worker or "MOONSHOT_API_KEY" in worker


def test_kimi_remains_advisory_not_a_protected_live_gate() -> None:
    existing = (ROOT / "tests" / "test_kimi_seventh_agent.py").read_text(encoding="utf-8")
    assert 'assert "kimi" not in cost.ALL_ADVISERS' in existing
