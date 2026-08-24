from __future__ import annotations

from pathlib import Path

from learnerbot import ai_council as council
from learnerbot import grok_provider
from learnerbot import kimi_provider
from learnerbot import ai_cost_provider_patch as cost_provider
from scripts import ai_agent_bus
from scripts import ai_agent_bus_provider_compat

ROOT = Path(__file__).resolve().parents[1]


def test_grok_provider_remains_in_council_and_under_kimi_chain() -> None:
    grok_provider.install()
    kimi_provider.install()
    assert "grok" in council.PROVIDERS
    assert "grok" in council.LEADERS
    assert council.call_provider is cost_provider.call_provider
    assert cost_provider._ORIGINAL_CALL_PROVIDER is kimi_provider.call_provider
    assert kimi_provider._BASE_HTTP_CALL is grok_provider.call_provider


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


def test_central_factory_collects_grok_with_all_agents_and_gpt_master() -> None:
    central = (ROOT / "scripts" / "central_report_scheduler.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert "ops._panel_for = lambda package: list(AGENTS)" in central
    assert 'out["master"] = "gpt"' in central


def test_grok_is_present_in_persistent_strategy_factory_runtime() -> None:
    runtime = (ROOT / "learnerbot" / "ai_agent_ws_runtime_patch.py").read_text(encoding="utf-8")
    worker = (ROOT / "scripts" / "ai_agent_ws_worker.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in runtime
    assert '"grok", "kimi", "copilot"' in worker
    assert '"grok": ("XAI_COUNCIL_MODEL", "grok-4.20-non-reasoning")' in worker


def test_four_hour_provider_preflight_checks_grok_without_model_inference() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-provider-preflight.yml").read_text(encoding="utf-8")
    assert "cron: '11 */4 * * *'" in workflow
    assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in workflow
    assert "check_grok()" in workflow
    assert "https://api.x.ai/v1/models" in workflow
    assert "'grok':one('xai')" in workflow
    assert "'paid_inference_requested':False" in workflow
