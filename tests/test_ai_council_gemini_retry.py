from __future__ import annotations

from learnerbot import ai_council
from learnerbot import ai_council_http_patch


def _legacy_call_provider(provider: str, prompt: str):
    """Exercise the preserved CLI provider path, not the installed live HTTP patch."""
    return ai_council_http_patch._BASE_CALL_PROVIDER(provider, prompt)


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

    rc, out, err = _legacy_call_provider("gemini", "hello")

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
    assert _legacy_call_provider("gemini", "hello") == (0, "OK", "")


def test_gemini_non_429_error_is_not_retried(monkeypatch):
    calls = []

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fake_run(cmd, prompt, env, *, stdin=False):
        calls.append(list(cmd))
        return 1, "", "HTTP 400 invalid request"

    monkeypatch.setattr(ai_council, "_run", fake_run)
    rc, out, err = _legacy_call_provider("gemini", "hello")

    assert rc == 1
    assert out == ""
    assert "400" in err
    assert len(calls) == 1
