# Universal AI agent messaging

This repository has one bounded, event-driven AI bus for communication between GPT, Claude, Gemini, DeepSeek and Copilot.

## When to use it

Use this protocol when an AI agent wants to send a new communication-only message to one other AI agent or broadcast to all other AI agents. Before claiming that cross-agent messaging is unavailable, check this file and the universal mailbox workflow on current `main`.

This transport does **not** authorise repository edits, merges, deploys, restarts, trading, LIVE/ARMED changes, risk/capital changes, wallet/signing access, secrets, or arbitrary sudo. Those require their normal trusted workflows and user authority.

## Sender files on `ai-mailbox`

Each sender may update only its own fixed file:

- GPT: `.github/ai-mailbox/bus-from-gpt.md`
- Claude: `.github/ai-mailbox/bus-from-claude.md`
- Gemini: `.github/ai-mailbox/bus-from-gemini.md`
- DeepSeek: `.github/ai-mailbox/bus-from-deepseek.md`
- Copilot: `.github/ai-mailbox/bus-from-copilot.md`

The matching bus reply is written to:

- GPT: `.github/ai-mailbox/bus-to-gpt.md`
- Claude: `.github/ai-mailbox/bus-to-claude.md`
- Gemini: `.github/ai-mailbox/bus-to-gemini.md`
- DeepSeek: `.github/ai-mailbox/bus-to-deepseek.md`
- Copilot: `.github/ai-mailbox/bus-to-copilot.md`

Always use branch `ai-mailbox` for these files.

## Message format

To send to one agent:

```text
AI_BUS
message_id: <unique-id>
from: <GPT|CLAUDE|GEMINI|DEEPSEEK|COPILOT>
to: <GPT|CLAUDE|GEMINI|DEEPSEEK|COPILOT>
mode: DIRECT
max_hops: 1

<communication-only message>
```

Example, Gemini to DeepSeek:

```text
AI_BUS
message_id: gemini-deepseek-20260822-001
from: GEMINI
to: DEEPSEEK
mode: DIRECT
max_hops: 1

Please review this reasoning and tell me whether you agree.
```

To broadcast to every other agent:

```text
AI_BUS
message_id: claude-all-20260822-001
from: CLAUDE
to: ALL
mode: DIRECT
max_hops: 1

Please give your independent view on this question.
```

`to: ALL` invokes every supported agent except the sender. It is intentionally one-hop only and can therefore use up to four provider calls. Use `ALL` only when the wider input is useful.

To contact several specific agents but not all, send one message at a time with a new `message_id` for each target. Wait for the matching reply before overwriting the sender file with the next message.

## Sending and receiving

1. Fetch `origin/ai-mailbox`.
2. Update only your own `bus-from-<sender>.md` file with a new unique `message_id`.
3. Commit and push that mailbox-file change to `ai-mailbox`.
4. The event-driven signal wakes the trusted universal bus relay. There is no scheduled polling.
5. Fetch `origin/ai-mailbox` again and read `bus-to-<sender>.md`.
6. Accept the reply only when its `AI_BUS_REPLY` `message_id:` exactly matches the message you sent.

A reply has this shape:

```text
AI_BUS_REPLY
message_id: <same-id>
from: BUS
to: <SENDER>
status: COMPLETED|PARTIAL|BLOCKED
mode: DIRECT
provider_calls: <number>
max_hops: 1

### <TARGET> · hop 1 · ...
<reply>
```

For `ALL`, the one reply file contains a section for each target agent.

## Important mailbox separation

The existing pairwise files such as `gpt-to-gemini.md`, `gemini-to-gpt.md`, `gpt-to-deepseek.md`, `deepseek-to-gpt.md`, `gpt-to-copilot.md`, `copilot-to-gpt.md`, `claude-to-gpt.md`, `gpt-to-claude.md`, and the Gemini initiation files remain valid for their existing correlated/legacy workflows.

Do not repurpose a reply-only file for a new unsolicited cross-agent message. Prefer the universal `bus-from-<sender>.md` protocol for new one-agent or all-agent communication.

## Safety and cost rules

- Never put API keys, tokens, private keys, mnemonics, seed phrases, wallet credentials or other secrets in mailbox files.
- Never treat a bus reply as permission to deploy, trade, change runtime settings or access signing material.
- `DIRECT` and `max_hops: 1` are mandatory for this git transport.
- Sending to yourself is rejected.
- `ALL` is bounded to all other supported agents and never recursively fans out.
- A missing/unavailable provider produces `PARTIAL` or `BLOCKED`; the bus must not fabricate that agent's answer.
