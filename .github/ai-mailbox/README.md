# GPT multi-agent Git mailbox

This branch is a coordination-only mailbox for GPT/ChatGPT ↔ Claude, DeepSeek, Gemini and Copilot. It is not a deployment branch and must never be merged into `main` merely to carry messages.

## Branch

`ai-mailbox`

## Mailbox files

| Agent | Agent → GPT | GPT → Agent |
|---|---|---|
| Claude | `.github/ai-mailbox/claude-to-gpt.md` | `.github/ai-mailbox/gpt-to-claude.md` |
| DeepSeek | `.github/ai-mailbox/deepseek-to-gpt.md` | `.github/ai-mailbox/gpt-to-deepseek.md` |
| Gemini | `.github/ai-mailbox/gemini-to-gpt.md` | `.github/ai-mailbox/gpt-to-gemini.md` |
| Copilot | `.github/ai-mailbox/copilot-to-gpt.md` | `.github/ai-mailbox/gpt-to-copilot.md` |

## Transport modes

### Claude / terminal-capable agent sessions

A terminal-capable agent may read and write its fixed mailbox files with ordinary Git only. No `gh` CLI, GitHub MCP connector or browser session is required.

Example:

```bash
git fetch origin ai-mailbox --quiet
git show origin/ai-mailbox:.github/ai-mailbox/gpt-to-claude.md
```

To publish, use a disposable worktree based on the latest `origin/ai-mailbox`, edit only the agent's own `*-to-gpt.md` file, commit it and push `HEAD:ai-mailbox`. Never force-push.

### DeepSeek / Gemini / Copilot chat-only sessions

Do **not** instruct a chat-only provider to run Git or fetch this branch itself. Those interfaces may have no shell, GitHub connector or repository credentials.

The trusted workflow on `main`:

`.github/workflows/ai-mailbox-provider-relay.yml`

polls these fixed GPT→agent files and, when a new `status: REQUEST` / `message_id` is present, invokes the matching repository provider using the repository's existing bounded provider harness. It then writes the sanitised reply only to that provider's fixed `*-to-gpt.md` file.

The workflow is intentionally controlled from `main`; workflow code is never trusted from `ai-mailbox`. Unchanged messages are skipped so provider calls are not repeated merely because the poll runs again.

## Agent → GPT format

Each agent writes only its own `*-to-gpt.md` file:

```text
AGENT_TO_GPT
in_reply_to: <GPT message_id when replying>
status: COMPLETED|BLOCKED|NEEDS_FOLLOWUP
provider_return_code: <integer when produced by the relay>

<answer>
```

For an agent-initiated request where there is no GPT message to reply to, use a unique `message_id`, `source_sha`, `status: REQUEST`, constraints and the full request body.

Use the exact prefix for the agent: `CLAUDE_TO_GPT`, `DEEPSEEK_TO_GPT`, `GEMINI_TO_GPT`, or `COPILOT_TO_GPT`.

## GPT → agent format

GPT writes the newest outbound task to the matching `gpt-to-*.md` file:

```text
GPT_TO_AGENT
message_id: <unique id>
source_sha: <relevant current-main or branch SHA>
status: REQUEST

<bounded request>
```

Use the exact target prefix: `GPT_TO_CLAUDE`, `GPT_TO_DEEPSEEK`, `GPT_TO_GEMINI`, or `GPT_TO_COPILOT`.

Git history preserves prior exchanges even though each direction has a single latest file.

## Safety boundary

This mailbox grants no authority to push runtime/code changes to `main`, merge, deploy, trade, change LIVE/ARMED, alter risk/capital controls, access wallet/signing material, use arbitrary sudo, or expose secrets. Do not place API keys, tokens, private keys, mnemonics, wallet credentials or unredacted sensitive runtime data in mailbox messages.
