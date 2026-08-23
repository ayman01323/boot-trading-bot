from __future__ import annotations

from pathlib import Path

from learnerbot import ai_council as council
from learnerbot import grok_provider
from scripts import ai_agent_bus
from scripts import ai_agent_bus_provider_compat

ROOT = Path(__file__).resolve().parents[1]


def test_grok_provider_is_sixth_council_member_and_leader() -> None:
    grok_provider.install()
    assert "grok" in council.PROVIDERS
    assert "grok" in council.LEADERS
    assert council.call_provider is grok_provider.call_provider


def test_grok_uses_bounded_xai_chat_completions(monkeypatch) -> None:
    seen = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = payload
        return 200, {"choices": [{"message": {"content": "grok answer"}}]}, "", {}

    monkeypatch.setattr(grok_provider._http, "_http_json", fake_http)
    rc, out, err = grok_provider.call_grok(
        "question",
        {"XAI_API_KEY": "xai-secret", "XAI_COUNCIL_MODEL": "grok-test"},
    )
    assert rc == 0
    assert out == "grok answer"
    assert err == ""
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer xai-secret"
    assert seen["payload"]["model"] == "grok-test"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "question"}]


def test_grok_missing_key_is_explicit() -> None:
    rc, out, err = grok_provider.call_grok("question", {})
    assert rc == 90
    assert out == ""
    assert "XAI_API_KEY" in err


def test_ai_bus_accepts_grok_and_redacts_xai_secret() -> None:
    ai_agent_bus_provider_compat.install()
    assert "grok" in ai_agent_bus.AGENTS
    assert "grok" in ai_agent_bus._AGENT_SET
    assert "XAI_API_KEY" in ai_agent_bus._SECRET_ENV_KEYS
    envelope = ai_agent_bus.parse_envelope(
        "AI_BUS\nmessage_id: grok-test-1\nfrom: USER\nto: GROK\nmode: DIRECT\n\nReview this architecture."
    )
    assert envelope.target == "grok"


def test_telegram_installs_grok_after_five_agent_layer() -> None:
    source = (ROOT / "learnerbot" / "telegram_command_scope_patch.py").read_text(encoding="utf-8")
    assert "telegram_five_agent_patch" in source
    assert "telegram_grok_council_patch" in source
    assert source.index("telegram_five_agent_patch") < source.index("telegram_grok_council_patch")


def test_runtime_secret_sync_includes_xai_without_printing_values() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-council-runtime-secrets.yml").read_text(encoding="utf-8")
    assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in workflow
    assert "'XAI_API_KEY'" in workflow
    assert "'grok': 'XAI_API_KEY' in present" in workflow
    assert 'cat "$target"' not in workflow


def test_ai_bus_workflow_passes_xai_secret_and_reports_grok() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-agent-bus.yml").read_text(encoding="utf-8")
    assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in workflow
    assert "XAI_COUNCIL_MODEL" in workflow
    assert "GROK" in workflow
