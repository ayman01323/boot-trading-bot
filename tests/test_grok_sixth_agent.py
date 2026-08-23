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
    # The final public hook remains budget-gated. Kimi is the newest adapter and
    # delegates every non-Kimi request to the pre-existing Grok-aware adapter.
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


def test_grok_is_selectable_master_without_importing_live_runtime() -> None:
    source = (ROOT / "learnerbot" / "ai_master_control.py").read_text(encoding="utf-8")
    assert 'PROVIDERS = ("auto", "gpt", "gemini", "copilot", "claude", "deepseek", "grok")' in source
    assert 'out[f"{lane}_master"] = master if master in PROVIDERS else "auto"' in source


def test_telegram_installs_grok_after_five_agent_layer() -> None:
    source = (ROOT / "learnerbot" / "telegram_command_scope_patch.py").read_text(encoding="utf-8")
    patch = (ROOT / "learnerbot" / "telegram_grok_council_patch.py").read_text(encoding="utf-8")
    assert "telegram_five_agent_patch" in source
    assert "telegram_grok_council_patch" in source
    assert source.index("telegram_five_agent_patch") < source.index("telegram_grok_council_patch")
    assert '"grok": "Grok"' in patch
    assert "six_agent_reports_complete" in patch
    assert "aicfg:master:{lane}:grok" in patch
    assert "SIX STRATEGY AGENTS COMPLETE" in patch


def test_runtime_secret_sync_includes_xai_without_printing_values() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-council-runtime-secrets.yml").read_text(encoding="utf-8")
    deploy = (ROOT / ".github" / "workflows" / "deploy-current-main-pr-isolated.yml").read_text(encoding="utf-8")
    for body in (workflow, deploy):
        assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in body
        assert "XAI_API_KEY" in body
    assert "'grok': 'XAI_API_KEY' in present" in workflow
    assert 'cat "$target"' not in workflow


def test_ai_bus_workflow_passes_xai_secret_and_reports_grok() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-agent-bus.yml").read_text(encoding="utf-8")
    assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in workflow
    assert "XAI_COUNCIL_MODEL" in workflow
    assert "GROK" in workflow


def test_grok_scheduled_reviewers_are_read_only_and_sandboxed() -> None:
    for name in ("grok-sixth-strategy-agent.yml", "grok-sixth-engineering-agent.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "@xai-official/grok@latest" in body
        assert "--permission-mode dontAsk" in body
        assert "--allow 'Read' --allow 'Grep'" in body
        assert "--deny 'Edit' --deny 'Bash(*)'" in body
        assert "--sandbox strict" in body
        assert "--disable-web-search" in body
        assert "no_live_changes" in body
        assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in body


def test_selected_master_collects_and_can_call_grok() -> None:
    workflow = (ROOT / ".github" / "workflows" / "selected-ai-master.yml").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "resilient_selected_master_v2.py").read_text(encoding="utf-8")
    assert '"Grok Sixth Strategy Agent"' in workflow
    assert '"Grok Sixth Engineering Agent"' in workflow
    assert "for provider in gpt gemini claude deepseek grok" in workflow
    assert "XAI_MASTER_MODEL" in workflow
    assert '"grok", "copilot"' in runner
    assert 'provider == "grok"' in runner
    assert "max(0, 6 - len(valid_reports))" in runner


def test_grok_is_present_in_persistent_strategy_factory_runtime() -> None:
    runtime = (ROOT / "learnerbot" / "ai_agent_ws_runtime_patch.py").read_text(encoding="utf-8")
    worker = (ROOT / "scripts" / "ai_agent_ws_worker.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in runtime
    assert '"grok", "kimi", "copilot"' in worker
    assert '"grok": ("XAI_COUNCIL_MODEL", "grok-4.20-non-reasoning")' in worker


def test_grok_is_wired_into_health_strategy_and_engineering_surfaces() -> None:
    patch = (ROOT / "learnerbot" / "telegram_grok_council_patch.py").read_text(encoding="utf-8")
    assert "_strategy_room.PROVIDERS = PROVIDERS" in patch
    assert "_health.PROVIDERS = PROVIDERS" in patch
    assert "_compact.PROVIDERS = PROVIDERS" in patch
    assert '_compact._LABELS["grok"] = "Grok"' in patch
    assert "engineering_status_six_agent" in patch
    assert "strategy_status_six_agent" in patch


def test_hourly_provider_preflight_checks_xai_grok() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-provider-preflight.yml").read_text(encoding="utf-8")
    assert "XAI_API_KEY: ${{ secrets.XAI_API_KEY }}" in workflow
    assert "check_xai()" in workflow
    assert "https://api.x.ai/v1/models" in workflow
    assert "'xai':one('xai')" in workflow


def test_seven_agent_live_diagnostic_probes_grok_kimi_and_all_other_agents() -> None:
    workflow = (ROOT / ".github" / "workflows" / "strategy-factory-six-agent-live-diagnostic.yml").read_text(encoding="utf-8")
    assert "{'gpt','claude','gemini','deepseek','grok','kimi','copilot'}" in workflow
    for provider in ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot"):
        assert f"probe gpt {provider}" in workflow or (provider == "gpt" and "probe gemini gpt" in workflow)
    assert "--to grok" in workflow
    assert "--to kimi" in workflow
    assert "kimi_bounded_server_task=COMPLETED" in workflow
