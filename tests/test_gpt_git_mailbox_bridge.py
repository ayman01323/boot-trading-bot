from scripts.gpt_git_mailbox_bridge import (
    build_claude_mailbox_reply,
    normalize_gpt_request,
    reply_to_request_id,
    request_id_from_gpt,
)


def test_request_requires_explicit_request_status():
    text = """GPT_TO_CLAUDE
message_id: test-1
status: REQUEST

Please assess the strategy.
"""
    assert request_id_from_gpt(text) == "test-1"
    assert request_id_from_gpt(text.replace("status: REQUEST", "status: COMPLETED")) == ""


def test_normalize_targets_claude_once():
    text = """GPT_TO_CLAUDE
message_id: test-2
status: REQUEST
constraints: analysis-only

Review this.
"""
    message_id, envelope = normalize_gpt_request(text)
    assert message_id == "test-2"
    assert envelope.startswith("AI_BUS\nmessage_id: test-2\nfrom: GPT\nto: CLAUDE\nmode: DIRECT\nmax_hops: 1")
    assert "GPT_TO_CLAUDE" in envelope


def test_claude_reply_is_response_and_dedupes_original_request():
    bus_reply = """AI_BUS_REPLY
message_id: test-3
from: BUS
to: GPT
status: COMPLETED
mode: DIRECT
provider_calls: 1
max_hops: 1

### CLAUDE · hop 1 · COMPLETED · rc 0

Looks sound; watch realised follower edge.
"""
    reply = build_claude_mailbox_reply("test-3", bus_reply)
    assert reply.startswith("CLAUDE_TO_GPT\nmessage_id: claude-reply-")
    assert "status: RESPONSE" in reply
    assert "in_reply_to: test-3" in reply
    assert reply_to_request_id(reply) == "test-3"
    assert "Looks sound" in reply
