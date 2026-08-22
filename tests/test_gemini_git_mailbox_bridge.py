from __future__ import annotations

import base64
import importlib.util
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "gemini_git_mailbox_bridge.py"
SPEC = importlib.util.spec_from_file_location("gemini_git_mailbox_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def gemini_message(message_id: str = "gemini-init-1") -> str:
    return (
        "GEMINI_TO_GPT_INIT\n"
        f"message_id: {message_id}\n"
        "source_sha: abc123\n"
        "status: REQUEST\n"
        "constraints: COMMUNICATION_ONLY; no secrets\n\n"
        "GPT, please review this bounded Gemini message.\n"
    )


def bus_reply(message_id: str = "gemini-init-1") -> str:
    return (
        "AI_BUS_REPLY\n"
        f"message_id: {message_id}\n"
        "from: BUS\n"
        "to: GEMINI\n"
        "status: COMPLETED\n"
        "mode: DIRECT\n"
        "provider_calls: 1\n"
        "max_hops: 1\n\n"
        "GPT received the Gemini message.\n"
    )


def test_normalize_routes_only_to_gpt() -> None:
    message_id, envelope = bridge.normalize_gemini_message(gemini_message())
    assert message_id == "gemini-init-1"
    assert envelope.startswith("AI_BUS\n")
    assert "from: GEMINI\n" in envelope
    assert "to: GPT\n" in envelope
    assert "mode: DIRECT\n" in envelope
    assert "max_hops: 1\n" in envelope


def test_invalid_prefix_status_or_message_id_is_rejected() -> None:
    invalid = (
        "NOT_GEMINI\nmessage_id: x\nstatus: REQUEST\n\nbody\n",
        "GEMINI_TO_GPT_INIT\nmessage_id: bad id\nstatus: REQUEST\n\nbody\n",
        "GEMINI_TO_GPT_INIT\nmessage_id: x\nstatus: COMPLETED\n\nbody\n",
    )
    for text in invalid:
        try:
            bridge.normalize_gemini_message(text)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid Gemini mailbox input was accepted")


def test_existing_reply_dedupes_exact_message(monkeypatch) -> None:
    incoming = gemini_message("done-1")
    outgoing = (
        "GPT_TO_GEMINI_INIT\n"
        "in_reply_to: done-1\n"
        "status: COMPLETED\n\n"
        "already handled\n"
    )

    def fake_fetch(repo: str, path: str, *, token: str):
        return (incoming, "in-sha") if path == bridge.GEMINI_TO_GPT_INIT_PATH else (outgoing, "out-sha")

    monkeypatch.setattr(bridge, "fetch_fixed_file", fake_fetch)
    pending, message_id, _ = bridge.select_pending("owner/repo", token="t")
    assert pending is False
    assert message_id == "done-1"


def test_new_message_is_pending(monkeypatch) -> None:
    incoming = gemini_message("new-1")
    outgoing = "GPT_TO_GEMINI_INIT\nin_reply_to: older\nstatus: COMPLETED\n\nold\n"

    def fake_fetch(repo: str, path: str, *, token: str):
        return (incoming, "in-sha") if path == bridge.GEMINI_TO_GPT_INIT_PATH else (outgoing, "out-sha")

    monkeypatch.setattr(bridge, "fetch_fixed_file", fake_fetch)
    pending, message_id, envelope = bridge.select_pending("owner/repo", token="t")
    assert pending is True
    assert message_id == "new-1"
    assert "from: GEMINI\n" in envelope
    assert "to: GPT\n" in envelope


def test_publish_writes_only_fixed_reply_path(monkeypatch) -> None:
    calls = []

    def fake_fetch(repo: str, path: str, *, token: str):
        assert path == bridge.GPT_TO_GEMINI_INIT_PATH
        return ("GPT_TO_GEMINI_INIT\nin_reply_to: old\nstatus: COMPLETED\n", "old-sha")

    def fake_json(url: str, *, token: str, method: str = "GET", payload=None):
        calls.append((url, method, payload))
        return {}

    monkeypatch.setattr(bridge, "fetch_fixed_file", fake_fetch)
    monkeypatch.setattr(bridge, "_github_json", fake_json)
    bridge.publish_reply("owner/repo", token="t", message_id="gemini-init-1", bus_reply=bus_reply())
    assert len(calls) == 1
    url, method, payload = calls[0]
    assert url.endswith("/contents/.github/ai-mailbox/gpt-to-gemini-init.md")
    assert method == "PUT"
    assert payload["branch"] == "ai-mailbox"
    assert payload["sha"] == "old-sha"
    decoded = base64.b64decode(payload["content"]).decode()
    assert decoded.startswith("GPT_TO_GEMINI_INIT\n")
    assert "in_reply_to: gemini-init-1\n" in decoded
    assert "communication-only" in decoded


def test_arbitrary_mailbox_path_is_rejected() -> None:
    try:
        bridge.fetch_fixed_file("owner/repo", ".env", token="t")
    except ValueError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("arbitrary path was accepted")


def test_signal_watches_only_gemini_initiating_path() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/gemini-mailbox-signal.yml").read_text())
    trigger = workflow.get("on") or workflow.get(True)
    assert trigger["push"]["branches"] == ["ai-mailbox"]
    assert trigger["push"]["paths"] == [".github/ai-mailbox/gemini-init-to-gpt.md"]
    assert workflow["jobs"]["signal"]["runs-on"] == ["self-hosted", "linux", "x64", "boot-vps"]


def test_bridge_is_event_driven_and_gpt_only() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/gemini-git-mailbox-bridge.yml").read_text())
    trigger = workflow.get("on") or workflow.get(True)
    assert "schedule" not in trigger
    assert "workflow_run" in trigger
    job = workflow["jobs"]["bridge"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "boot-vps"]
    text = (ROOT / ".github/workflows/gemini-git-mailbox-bridge.yml").read_text()
    assert 'test "$target" = "gpt"' in text
    assert "OPENAI_API_KEY" in text
    assert "GEMINI_API_KEY" not in text
    assert "deploy-boot-trading-bot" not in text
    assert not re.search(r"(?m)^\s*sudo\s+", text)
