from __future__ import annotations

from types import SimpleNamespace

import pytest

from learnerbot import ai_council


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def test_council_asks_all_five_agents_independently_in_parallel(tmp_path, monkeypatch):
    calls = []

    def fake_call(provider, prompt):
        calls.append((provider, prompt))
        assert "one independent member of SiBot's AI Council" in prompt
        assert "Other agent answer" not in prompt
        return 0, f"{provider} independent answer", ""

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _app(tmp_path)
    session = ai_council.create_session(app, 12345, "Should this strategy remain enabled?", mode="user")
    completed = ai_council.run_independent_answers(app, session["session_id"])

    assert set(completed["answers"]) == set(ai_council.PROVIDERS)
    assert {provider for provider, _ in calls} == set(ai_council.PROVIDERS)
    assert all(row["status"] == "DONE" for row in completed["answers"].values())
    assert completed["status"] == "ANSWERS_READY"


def test_second_leader_uses_same_original_answers_not_first_leader_output(tmp_path, monkeypatch):
    leader_prompts = {}

    def fake_call(provider, prompt):
        if "one independent member of SiBot's AI Council" in prompt:
            return 0, f"ORIGINAL-{provider}-ANSWER", ""
        if "selected as SiBot AI Council Leader" in prompt:
            leader_prompts[provider] = prompt
            return 0, f"LEADER-{provider}-FINAL", ""
        raise AssertionError("unexpected prompt")

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _app(tmp_path)
    session = ai_council.create_session(app, 999, "Compare the evidence.", mode="master")
    ai_council.run_independent_answers(app, session["session_id"])

    gpt = ai_council.run_leader(app, session["session_id"], "gpt")
    claude = ai_council.run_leader(app, session["session_id"], "claude")

    assert gpt["status"] == "DONE"
    assert claude["status"] == "DONE"
    for provider in ai_council.PROVIDERS:
        assert f"ORIGINAL-{provider}-ANSWER" in leader_prompts["gpt"]
        assert f"ORIGINAL-{provider}-ANSWER" in leader_prompts["claude"]
    assert "LEADER-gpt-FINAL" not in leader_prompts["claude"]

    persisted = ai_council.load_session(app, session["session_id"])
    assert persisted["leaders"]["gpt"]["answer"] == "LEADER-gpt-FINAL"
    assert persisted["leaders"]["claude"]["answer"] == "LEADER-claude-FINAL"


def test_failed_agent_is_recorded_but_does_not_block_other_answers(tmp_path, monkeypatch):
    def fake_call(provider, prompt):
        if provider == "copilot":
            return 90, "", "Copilot token unavailable"
        return 0, f"answer from {provider}", ""

    monkeypatch.setattr(ai_council, "call_provider", fake_call)
    app = _app(tmp_path)
    session = ai_council.create_session(app, 321, "Question", mode="user")
    completed = ai_council.run_independent_answers(app, session["session_id"])

    assert completed["answers"]["copilot"]["status"] == "FAILED"
    assert "token" in completed["answers"]["copilot"]["error"].lower()
    assert sum(row["status"] == "DONE" for row in completed["answers"].values()) == 4


def test_question_length_is_bounded(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(ai_council.CouncilError):
        ai_council.create_session(app, 1, "x" * (ai_council.MAX_QUESTION_CHARS + 1), mode="user")


def test_gemini_council_defaults_to_flash_and_retries_429(monkeypatch):
    calls = []
    sleeps = []

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_COUNCIL_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MASTER_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_STRATEGY_MODEL", raising=False)
    monkeypatch.setattr(ai_council.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(ai_council.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_run(cmd, prompt, env, *, stdin=False):
        calls.append(list(cmd))
        if len(calls) == 1:
            return 1, "", "HTTP 429 RESOURCE_EXHAUSTED Too Many Requests retry in 3s"
        return 0, "GEMINI_OK", ""

    monkeypatch.setattr(ai_council, "_run", fake_run)

    rc, out, err = ai_council.call_provider("gemini", "hello")

    assert rc == 0
    assert out == "GEMINI_OK"
    assert err == ""
    assert len(calls) == 2
    assert all("--model" in cmd for cmd in calls)
    assert all(cmd[cmd.index("--model") + 1] == "gemini-3.7-flash" for cmd in calls)
    assert sleeps == [3.0]


def test_gemini_council_model_override_wins(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_COUNCIL_MODEL", "gemini-custom-model")

    def fake_run(cmd, prompt, env, *, stdin=False):
        assert cmd[cmd.index("--model") + 1] == "gemini-custom-model"
        return 0, "OK", ""

    monkeypatch.setattr(ai_council, "_run", fake_run)
    assert ai_council.call_provider("gemini", "hello") == (0, "OK", "")
