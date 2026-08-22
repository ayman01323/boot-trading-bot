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


def test_normalize_targets_claude_api_once_and_discloses_identity():
    text = """GPT_TO_CLAUDE
message_id: test-2
status: REQUEST
constraints: analysis-only

Review this.
"""
    message_id, envelope = normalize_gpt_request(text)
    assert message_id == "test-2"
    assert envelope.startswith("AI_BUS\nmessage_id: test-2\nfrom: GPT\nto: CLAUDE\nmode: DIRECT\nmax_hops: 1")
    assert "stateless Anthropic API responder" in envelope


def test_claude_api_reply_is_not_persistent_claude_identity():
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
    assert reply.startswith("CLAUDE_API_TO_GPT\nmessage_id: claude-api-reply-")
    assert "identity: STATELESS_API_RESPONDER" in reply
    assert "persistent_agent: false" in reply
    assert "not a message authored by the persistent/interactive Claude agent" in reply
    assert "in_reply_to: test-3" in reply
    assert reply_to_request_id(reply) == "test-3"
