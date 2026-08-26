from pathlib import Path

import pytest
import yaml

from scripts import provider_git_mailbox_bridge as bridge


@pytest.mark.parametrize("provider", bridge.ALLOWED_PROVIDERS)
def test_fixed_paths_and_request_parsing(provider):
    message = (
        f"GPT_TO_{provider.upper()}\n"
        "message_id: solana-review-1\n"
        "status: REQUEST\n\n"
        "Review the current Solana strategy.\n"
    )
    assert bridge.request_path(provider) == f".github/ai-mailbox/gpt-to-{provider}.md"
    assert bridge.response_path(provider) == f".github/ai-mailbox/{provider}-to-gpt.md"
    assert bridge.request_id_from_gpt(provider, message) == "solana-review-1"


def test_grok_and_kimi_are_supported_fallback_providers():
    assert "grok" in bridge.ALLOWED_PROVIDERS
    assert "kimi" in bridge.ALLOWED_PROVIDERS
    assert bridge.request_path("grok") == ".github/ai-mailbox/gpt-to-grok.md"
    assert bridge.response_path("grok") == ".github/ai-mailbox/grok-to-gpt.md"
    assert bridge.request_path("kimi") == ".github/ai-mailbox/gpt-to-kimi.md"
    assert bridge.response_path("kimi") == ".github/ai-mailbox/kimi-to-gpt.md"


def test_non_request_cannot_invoke_provider():
    text = "GPT_TO_GEMINI\nmessage_id: x1\nstatus: COMPLETED\n\nDone\n"
    assert bridge.request_id_from_gpt("gemini", text) == ""


def test_select_pending_deduplicates(monkeypatch):
    incoming = "GPT_TO_DEEPSEEK\nmessage_id: x2\nstatus: REQUEST\n\nQuestion\n"
    outgoing = "DEEPSEEK_TO_GPT\nin_reply_to: x2\nstatus: COMPLETED\n\nAnswer\n"

    def fake_fetch(repo, provider, *, response, token):
        return (outgoing if response else incoming), "sha"

    monkeypatch.setattr(bridge, "fetch_provider_file", fake_fetch)
    pending, message_id, payload = bridge.select_pending("owner/repo", "deepseek", token="token")
    assert pending is False
    assert message_id == "x2"
    assert payload == incoming


def test_validate_reply_requires_matching_provider_and_request():
    good = "GEMINI_TO_GPT\nin_reply_to: x3\nstatus: COMPLETED\nprovider_return_code: 0\n\nAnswer\n"
    bridge.validate_provider_reply("gemini", "x3", good)
    with pytest.raises(ValueError):
        bridge.validate_provider_reply("deepseek", "x3", good)
    with pytest.raises(ValueError):
        bridge.validate_provider_reply("gemini", "other", good)


def test_provider_relay_is_event_driven_without_schedule():
    workflow = yaml.safe_load(Path(".github/workflows/ai-mailbox-provider-relay.yml").read_text())
    trigger = workflow.get("on") or workflow.get(True)
    assert "schedule" not in trigger
    assert "workflow_run" in trigger
    # Provider traffic runs on the dedicated Google communication runner and must
    # never occupy the production boot-vps deployment queue.
    assert workflow["jobs"]["relay"]["runs-on"] == ["self-hosted", "linux", "x64", "boot-google"]
    assert "boot-vps" not in workflow["jobs"]["relay"]["runs-on"]
    assert workflow["jobs"]["relay"]["strategy"]["matrix"]["provider"] == [
        "deepseek", "gemini", "grok", "kimi", "copilot"
    ]
    route_step = next(
        step for step in workflow["jobs"]["relay"]["steps"]
        if step.get("name") == "Ask provider once using fallback request content only"
    )
    route_env = route_step["env"]
    assert "XAI_API_KEY" in route_env
    assert "XAI_COUNCIL_MODEL" in route_env
    assert "KIMI_API_KEY" in route_env
    assert "MOONSHOT_API_KEY" in route_env
    assert "KIMI_COUNCIL_MODEL" in route_env
    assert "deepseek-v4-pro" in str(route_env["DEEPSEEK_COUNCIL_MODEL"])
    assert "gemini-3.5-flash-lite" in str(route_env["GEMINI_COUNCIL_MODEL"])


def test_signal_watches_all_provider_request_files_including_grok_and_kimi():
    workflow = yaml.safe_load(Path(".github/workflows/ai-mailbox-provider-signal.yml").read_text())
    trigger = workflow.get("on") or workflow.get(True)
    paths = trigger["push"]["paths"]
    assert paths == [
        ".github/ai-mailbox/gpt-to-deepseek.md",
        ".github/ai-mailbox/gpt-to-gemini.md",
        ".github/ai-mailbox/gpt-to-grok.md",
        ".github/ai-mailbox/gpt-to-kimi.md",
        ".github/ai-mailbox/gpt-to-copilot.md",
    ]
