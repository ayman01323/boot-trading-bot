from __future__ import annotations

import base64
import re
import unittest
from pathlib import Path
from unittest import mock

from scripts import universal_agent_git_mailbox_bridge as bridge


class UniversalAgentMailboxTests(unittest.TestCase):
    def message(self, sender: str, target: str, message_id: str = "msg-001") -> str:
        return (
            "AI_BUS\n"
            f"message_id: {message_id}\n"
            f"from: {sender.upper()}\n"
            f"to: {target.upper()}\n"
            "mode: DIRECT\n"
            "max_hops: 1\n\n"
            "Communication-only test.\n"
        )

    def reply(self, sender: str, message_id: str = "msg-001", status: str = "COMPLETED") -> str:
        return (
            "AI_BUS_REPLY\n"
            f"message_id: {message_id}\n"
            "from: BUS\n"
            f"to: {sender.upper()}\n"
            f"status: {status}\n"
            "mode: DIRECT\n"
            "provider_calls: 1\n"
            "max_hops: 1\n\n"
            "### GPT · hop 1 · COMPLETED · rc 0\n\nOK\n"
        )

    def test_every_sender_can_target_every_other_agent(self) -> None:
        for sender in bridge.AGENTS:
            for target in bridge.AGENTS:
                if sender == target:
                    continue
                message_id, parsed_target, envelope = bridge.normalize_sender_message(
                    self.message(sender, target, f"{sender}-to-{target}"), sender
                )
                self.assertEqual(message_id, f"{sender}-to-{target}")
                self.assertEqual(parsed_target, target)
                self.assertIn("mode: DIRECT", envelope)

    def test_every_sender_can_broadcast_all(self) -> None:
        for sender in bridge.AGENTS:
            message_id, target, _ = bridge.normalize_sender_message(
                self.message(sender, "all", f"{sender}-all"), sender
            )
            self.assertEqual(message_id, f"{sender}-all")
            self.assertEqual(target, "all")

    def test_spoofed_sender_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            bridge.normalize_sender_message(self.message("gemini", "gpt"), "deepseek")

    def test_self_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot target itself"):
            bridge.normalize_sender_message(self.message("claude", "claude"), "claude")

    def test_collaborate_and_multihop_are_rejected(self) -> None:
        text = self.message("gpt", "gemini").replace("mode: DIRECT", "mode: COLLABORATE")
        with self.assertRaisesRegex(ValueError, "DIRECT"):
            bridge.normalize_sender_message(text, "gpt")
        text = self.message("gpt", "gemini").replace("max_hops: 1", "max_hops: 2")
        with self.assertRaisesRegex(ValueError, "max_hops"):
            bridge.normalize_sender_message(text, "gpt")

    def test_reply_must_match_sender_and_message_id(self) -> None:
        self.assertEqual(bridge.reply_to_message_id(self.reply("gemini"), "gemini"), "msg-001")
        self.assertEqual(bridge.reply_to_message_id(self.reply("deepseek"), "gemini"), "")
        with self.assertRaisesRegex(ValueError, "message_id mismatch"):
            bridge.validate_bus_reply("other-id", "gemini", self.reply("gemini"))

    def test_arbitrary_mailbox_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "path is not allowed"):
            bridge.fetch_fixed_file("owner/repo", ".env", token="x")

    def test_select_pending_dedupes_exact_reply(self) -> None:
        incoming = self.message("deepseek", "gemini")
        outgoing = self.reply("deepseek")

        def fake_fetch(repo: str, path: str, *, token: str):
            if path == bridge.INCOMING_PATHS["deepseek"]:
                return incoming, "in-sha"
            if path == bridge.OUTGOING_PATHS["deepseek"]:
                return outgoing, "out-sha"
            raise AssertionError(path)

        with mock.patch.object(bridge, "fetch_fixed_file", side_effect=fake_fetch):
            pending, message_id, target, _ = bridge.select_pending(
                "owner/repo", token="x", sender="deepseek"
            )
        self.assertFalse(pending)
        self.assertEqual(message_id, "msg-001")
        self.assertEqual(target, "gemini")

    def test_publish_writes_only_fixed_sender_reply_path(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def fake_fetch(repo: str, path: str, *, token: str):
            self.assertEqual(path, bridge.OUTGOING_PATHS["copilot"])
            raise RuntimeError("GitHub API HTTP 404")

        def fake_json(url: str, *, token: str, method: str = "GET", payload=None):
            calls.append((url, method, payload or {}))
            return {}

        with mock.patch.object(bridge, "fetch_fixed_file", side_effect=fake_fetch), mock.patch.object(
            bridge, "_github_json", side_effect=fake_json
        ):
            bridge.publish_reply(
                "owner/repo",
                token="x",
                sender="copilot",
                message_id="msg-001",
                bus_reply=self.reply("copilot"),
            )

        self.assertEqual(len(calls), 1)
        url, method, payload = calls[0]
        self.assertTrue(url.endswith("/.github/ai-mailbox/bus-to-copilot.md"))
        self.assertEqual(method, "PUT")
        self.assertEqual(payload["branch"], "ai-mailbox")
        decoded = base64.b64decode(payload["content"]).decode()
        self.assertIn("to: COPILOT", decoded)

    def test_signal_and_relay_are_event_driven_and_bounded(self) -> None:
        signal = Path(".github/workflows/universal-ai-bus-mailbox-signal.yml").read_text()
        relay = Path(".github/workflows/universal-ai-bus-mailbox-relay.yml").read_text()
        for agent in bridge.AGENTS:
            self.assertIn(f"bus-from-{agent}.md", signal)
            self.assertIn(agent, relay)
        self.assertIn("workflow_run:", relay)
        self.assertIn("Universal AI Bus Mailbox Signal", relay)
        self.assertNotIn("schedule:", signal)
        self.assertNotIn("schedule:", relay)
        self.assertIn("mode=", relay)
        self.assertIn("test \"$mode\" = direct", relay)
        self.assertIn("OPENAI_API_KEY", relay)
        self.assertIn("ANTHROPIC_API_KEY", relay)
        self.assertIn("GEMINI_API_KEY", relay)
        self.assertIn("DEEPSEEK_API_KEY", relay)
        self.assertIn("COPILOT_ASSIGN_TOKEN", relay)
        self.assertNotRegex(relay, re.compile(r"(?m)^\s*sudo\s+"))
        self.assertNotIn("deploy-boot-trading-bot", relay)
        self.assertNotIn("protected deploy", relay.lower())

    def test_all_agent_instruction_files_teach_automatic_wake_up(self) -> None:
        for path in ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "DEEPSEEK.md"]:
            text = Path(path).read_text()
            self.assertIn("AI_AGENT_MESSAGING.md", text, path)
            self.assertIn("Delivery is automatic and event-driven", text, path)
            self.assertIn("does **not** poll", text, path)
            self.assertIn("Example send:", text, path)
            self.assertIn("AI_BUS", text, path)
        guide = Path("AI_AGENT_MESSAGING.md").read_text()
        self.assertIn("Automatic recipient wake-up", guide)
        self.assertIn("immediately invokes only the addressed provider", guide)
        self.assertIn("No recipient has to poll", guide)
        self.assertIn("not cryptographic proof of model identity", guide)
        self.assertIn("Example, GPT to Claude:", guide)


if __name__ == "__main__":
    unittest.main()
