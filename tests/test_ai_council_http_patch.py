from __future__ import annotations

from pathlib import Path

from learnerbot import ai_council as council
from learnerbot import ai_council_http_patch as http_patch
from learnerbot import ai_runtime_secret_fallback_patch as secret_fallback

ROOT = Path(__file__).resolve().parents[1]


def test_http_provider_patch_is_installed_before_friendly_ui() -> None:
    source = (ROOT / "learnerbot" / "telegram_solana_force_exit_patch.py").read_text(encoding="utf-8")
    assert "ai_council_http_patch" in source
    assert source.index("AI_COUNCIL_RUNTIME_ENV") < source.index("ai_council_http_patch")
    assert '"/var/tmp/ai_council_runtime.env"' in source
    assert source.index("ai_council_http_patch") < source.index("telegram_ai_council_friendly_patch")
    assert council.call_provider is http_patch.call_provider


def test_runtime_secret_bridge_overrides_provider_secret_only(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "ai_council_runtime.env"
    runtime.write_text(
        'OPENAI_API_KEY="bridge-openai"\n'
        'GEMINI_API_KEY="bridge-gemini"\n',
        encoding="utf-8",
    )
    repo_env = tmp_path / ".env"
    repo_env.write_text(
        'OPENAI_API_KEY="repo-openai"\n'
        'OPENAI_COUNCIL_MODEL="gpt-5.6-luna"\n',
        encoding="utf-8",
    )
    # The production process installs a second synced-runtime fallback at
    # /var/tmp/ai_council_runtime.env. Unit tests must never read that live file:
    # doing so can both make the test order-dependent and disclose a real secret
    # in pytest assertion output. Point the fallback at a deliberately absent
    # temporary file before exercising the base runtime bridge.
    monkeypatch.setattr(secret_fallback, "_SYNCED_RUNTIME_ENV", tmp_path / "no-live-synced-runtime.env")
    monkeypatch.setattr(http_patch, "_RUNTIME_ENV", runtime)
    monkeypatch.setattr(http_patch, "_REPO_ENV", repo_env)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COUNCIL_MODEL", raising=False)

    env = http_patch._runtime_env()
    assert env["OPENAI_API_KEY"] == "bridge-openai"
    assert env["GEMINI_API_KEY"] == "bridge-gemini"
    assert env["OPENAI_COUNCIL_MODEL"] == "gpt-5.6-luna"


def test_openai_council_uses_responses_api_and_parses_text(monkeypatch) -> None:
    seen = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = payload
        return 200, {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "cats need complete food"}],
                }
            ]
        }, "", {}

    monkeypatch.setattr(http_patch, "_http_json", fake_http)
    rc, out, err = http_patch._call_openai(
        "question",
        {"OPENAI_API_KEY": "secret", "OPENAI_COUNCIL_MODEL": "gpt-5.6-terra"},
    )
    assert rc == 0
    assert out == "cats need complete food"
    assert not err
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["payload"]["model"] == "gpt-5.6-terra"
    assert seen["payload"]["input"] == "question"


def test_gemini_direct_api_retries_429_without_leaking_key(monkeypatch) -> None:
    calls = []
    inference_attempts = 0

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        nonlocal inference_attempts
        calls.append((url, dict(headers), payload))
        if url.endswith("/models"):
            return 200, {
                "models": [{
                    "name": "models/gemini-3.5-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                }]
            }, "", {}
        inference_attempts += 1
        if inference_attempts == 1:
            return 429, {"error": {"message": "quota busy"}}, "quota busy", {"Retry-After": "1"}
        return 200, {
            "candidates": [{"content": {"parts": [{"text": "gemini answer"}]}}]
        }, "", {}

    monkeypatch.setattr(http_patch, "_http_json", fake_http)
    monkeypatch.setattr(http_patch.time, "sleep", lambda _: None)
    rc, out, err = http_patch._call_gemini(
        "question",
        {"GEMINI_API_KEY": "gemini-secret", "GEMINI_COUNCIL_MODEL": "gemini-3.5-flash-lite"},
    )
    assert rc == 0
    assert out == "gemini answer"
    assert err == ""
    assert inference_attempts == 2
    assert len(calls) == 3
    for url, headers, _ in calls:
        assert "gemini-secret" not in url
        assert headers.get("x-goog-api-key") == "gemini-secret"


def test_claude_discovers_sonnet_and_uses_messages_api(monkeypatch) -> None:
    calls = []

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        calls.append((url, payload))
        if url.startswith("https://api.anthropic.com/v1/models"):
            return 200, {"data": [{"id": "claude-sonnet-current"}, {"id": "claude-haiku-current"}]}, "", {}
        return 200, {"content": [{"type": "text", "text": "claude answer"}]}, "", {}

    monkeypatch.setattr(http_patch, "_http_json", fake_http)
    rc, out, err = http_patch._call_claude("question", {"ANTHROPIC_API_KEY": "secret"})
    assert rc == 0
    assert out == "claude answer"
    assert err == ""
    assert calls[-1][0] == "https://api.anthropic.com/v1/messages"
    assert calls[-1][1]["model"] == "claude-sonnet-current"


def test_deepseek_discovers_available_chat_model(monkeypatch) -> None:
    calls = []

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        calls.append((url, payload))
        if url == "https://api.deepseek.com/models":
            return 200, {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}, "", {}
        return 200, {"choices": [{"message": {"content": "deepseek answer"}}]}, "", {}

    monkeypatch.setattr(http_patch, "_http_json", fake_http)
    rc, out, err = http_patch._call_deepseek(
        "question",
        {"DEEPSEEK_API_KEY": "secret", "DEEPSEEK_MASTER_MODEL": "deepseek-v4-flash"},
    )
    assert rc == 0
    assert out == "deepseek answer"
    assert err == ""
    assert calls[-1][1]["model"] == "deepseek-chat"


def test_provider_error_redacts_secret() -> None:
    detail = http_patch._error_detail(
        401,
        {"error": {"message": "bad token secret-value"}},
        "",
        {"OPENAI_API_KEY": "secret-value"},
    )
    assert "secret-value" not in detail
    assert "[REDACTED]" in detail


def test_runtime_secret_workflow_never_prints_credential_file() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-council-runtime-secrets.yml").read_text(encoding="utf-8")
    isolated = (ROOT / ".github" / "workflows" / "deploy-current-main-pr-isolated.yml").read_text(encoding="utf-8")
    for body in (workflow, isolated):
        assert "/var/tmp/ai_council_runtime.env" in body
        assert "chmod 600" in body
    # Current main intentionally mirrors the root-readable bridge into the
    # Strategy Factory compatibility path. Safety means both files stay mode 600
    # and their contents are never printed, not that the compatibility path is absent.
    assert "/var/tmp/boot/ai_council_runtime.env" in workflow
    assert 'cat "$target"' not in workflow
    assert 'cat "$compat"' not in workflow
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in workflow
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in workflow
