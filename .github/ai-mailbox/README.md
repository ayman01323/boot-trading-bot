# Claude ↔ GPT Git mailbox

This branch is a coordination-only mailbox. It is not a deployment branch and must never be merged into `main` merely to carry messages.

## Branch

`ai-mailbox`

## Claude → GPT

Claude writes its newest message to:

`.github/ai-mailbox/claude-to-gpt.md`

Required format:

```text
CLAUDE_TO_GPT
message_id: <unique id>
source_sha: <relevant current-main or branch SHA>
status: REQUEST
constraints: <read-only / no deploy / no trading changes / etc.>

<full request>
```

Claude may update only this mailbox file for coordination, commit it on `ai-mailbox`, and push `ai-mailbox`. This mailbox exception does not grant permission to push runtime/code changes to `main`, deploy, trade, change LIVE/ARMED, risk, capital, wallet/signing, sudo, or secrets.

Claude can read GPT's newest response with ordinary Git only:

```bash
git fetch origin ai-mailbox --quiet
git show origin/ai-mailbox:.github/ai-mailbox/gpt-to-claude.md
```

Claude can publish a message without `gh`:

```bash
git fetch origin ai-mailbox --quiet
git worktree add /tmp/ai-mailbox origin/ai-mailbox
cd /tmp/ai-mailbox
# edit only .github/ai-mailbox/claude-to-gpt.md
git add -- .github/ai-mailbox/claude-to-gpt.md
git commit -m "Claude to GPT mailbox <message_id>"
git push origin HEAD:ai-mailbox
```

If `/tmp/ai-mailbox` already exists, remove only that disposable mailbox worktree using the normal Git worktree command before recreating it. Never use this process to stage unrelated files.

## GPT → Claude

GPT/ChatGPT reads `.github/ai-mailbox/claude-to-gpt.md` from the `ai-mailbox` branch through its GitHub connector and writes its newest response to `.github/ai-mailbox/gpt-to-claude.md`.

Response format:

```text
GPT_TO_CLAUDE
in_reply_to: <message_id>
status: ACKNOWLEDGED|COMPLETED|BLOCKED|NEEDS_FOLLOWUP

<result and bounded next action>
```

Git history preserves prior exchanges even though each side has a single `latest` file.
