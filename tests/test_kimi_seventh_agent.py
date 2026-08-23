from __future__ import annotations

from pathlib import Path

from learnerbot import ai_cost_router as cost
from learnerbot import kimi_provider
from scripts import ai_agent_ws_worker
from scripts import strategy_factory_transport

ROOT = Path(__file__).resolve().parents[1]


def test_kimi_uses_bounded_openai_compatible_api(monkeypatch) -> None:
    seen = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = payload
        return 200, {"choices": [{"message": {"content": "kimi answer"}}]}, "", {}

    monkeypatch.setattr(kimi_provider._http, "_http_json", fake_http)
    rc, out, err = kimi_provider.call_kimi("question", {"KIMI_API_KEY": "secret"})

    assert rc == 0
    assert out == "kimi answer"
    assert err == ""
    assert seen["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["payload"]["model"] == "kimi-k2.6"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "question"}]
    assert seen["payload"]["thinking"] == {"type": "disabled"}
    assert seen["payload"]["max_tokens"] == 2400


def test_kimi_accepts_moonshot_key_and_runtime_overrides(monkeypatch) -> None:
    seen = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        seen["url"] = url
        seen["payload"] = payload
        return 200, {"choices": [{"message": {"content": "ok"}}]}, "", {}

    monkeypatch.setattr(kimi_provider._http, "_http_json", fake_http)
    rc, out, err = kimi_provider.call_kimi(
        "question",
        {
            "MOONSHOT_API_KEY": "secret",
            "KIMI_BASE_URL": "https://example.invalid/v1/",
            "KIMI_COUNCIL_MODEL": "kimi-k3-test",
        },
    )
    assert (rc, out, err) == (0, "ok", "")
    assert seen["url"] == "https://example.invalid/v1/chat/completions"
    assert seen["payload"]["model"] == "kimi-k3-test"
    assert "thinking" not in seen["payload"]


def test_kimi_missing_key_is_explicit() -> None:
    rc, out, err = kimi_provider.call_kimi("question", {})
    assert rc == 90
    assert out == ""
    assert "KIMI_API_KEY" in err
    assert "MOONSHOT_API_KEY" in err


def test_kimi_is_seventh_strategy_factory_recipient() -> None:
    assert "kimi" in strategy_factory_transport.AGENTS
    assert "kimi" in ai_agent_ws_worker.AGENTS
    assert ai_agent_ws_worker.CHEAP_MODELS["kimi"] == ("KIMI_COUNCIL_MODEL", "kimi-k2.6")


def test_kimi_runtime_spawns_seventh_worker_without_changing_protected_quorum() -> None:
    runtime = (ROOT / "learnerbot" / "ai_agent_ws_runtime_patch.py").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_ai_agent_ws_bus.sh").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in runtime
    assert "gpt claude gemini deepseek grok kimi copilot" in installer
    # Credential activation is staged. Kimi must not become a mandatory protected
    # MASTER-change adviser until a real live API diagnostic has passed.
    assert "kimi" not in cost.ALL_ADVISERS


def test_kimi_runtime_secret_sync_is_redacted() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-council-runtime-secrets.yml").read_text(encoding="utf-8")
    assert "KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}" in workflow
    assert "MOONSHOT_API_KEY: ${{ secrets.MOONSHOT_API_KEY }}" in workflow
    assert "'kimi': ('KIMI_API_KEY' in present or 'MOONSHOT_API_KEY' in present)" in workflow
    assert 'cat "$target"' not in workflow
