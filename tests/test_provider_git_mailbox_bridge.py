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


def test_grok_is_a_supported_fallback_provider():
    assert "grok" in bridge.ALLOWED_PROVIDERS
    assert bridge.request_path("grok") == ".github/ai-mailbox/gpt-to-grok.md"
    assert bridge.response_path("grok") == ".github/ai-mailbox/grok-to-gpt.md"


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
    # This relay only needs GitHub/provider APIs. Keeping it on a hosted runner
    # prevents communication work from starving the production boot-vps deploy queue.
    assert workflow["jobs"]["relay"]["runs-on"] == "ubuntu-latest"
    assert workflow["jobs"]["relay"]["strategy"]["matrix"]["provider"] == ["deepseek", "gemini", "grok", "copilot"]
    route_env = workflow["jobs"]["relay"]["steps"][5]["env"]
    assert "XAI_API_KEY" in route_env
    assert "XAI_COUNCIL_MODEL" in route_env


def test_signal_watches_all_provider_request_files_including_grok():
    workflow = yaml.safe_load(Path(".github/workflows/ai-mailbox-provider-signal.yml").read_text())
    trigger = workflow.get("on") or workflow.get(True)
    paths = trigger["push"]["paths"]
    assert paths == [
        ".github/ai-mailbox/gpt-to-deepseek.md",
        ".github/ai-mailbox/gpt-to-gemini.md",
        ".github/ai-mailbox/gpt-to-grok.md",
        ".github/ai-mailbox/gpt-to-copilot.md",
    ]