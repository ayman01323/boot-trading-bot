# DeepSeek repository instructions

## Strategy Room mailbox awareness

At the start of every persistent DeepSeek repository session, and before claiming GPT has not sent a message, fetch `origin/ai-mailbox` when Git access is available and inspect `.github/ai-mailbox/gpt-to-deepseek.md`. Treat a new request as an unread Strategy Room message. GitHub can immediately invoke the stateless DeepSeek API responder, but it cannot inject a UI push into an already-open external session; this mandatory inbox check covers the persistent-session case. Keep persistent-session authorship distinct from automated API replies in `.github/ai-mailbox/deepseek-to-gpt.md`.

## Mandatory ChatGPT ↔ DeepSeek handoff protocol

Before DeepSeek analyses, edits, tests, commits, or pushes in this repository:

1. Run `git fetch origin main --quiet` when repository Git access is available.
2. Read the current handoff with `git show origin/main:.github/deepseek-handoff.md`.
3. If it contains a `handoff_id`, the first substantive response must include exactly:

   `DEEPSEEK_HANDOFF_ACK: <handoff_id>`

4. Follow the handoff's `status`, `scope`, `do_not_do`, and `message` fields. A newer handoff supersedes an older one.
5. If the current handoff cannot be read, stop before repository changes and report `DEEPSEEK_HANDOFF_READ_FAILED`.
6. Re-fetch and re-read the handoff immediately before any push. If the ID changed, acknowledge and follow the newer handoff first.
7. A handoff never authorises merge/deploy/trading or weakening wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared DeepSeek inbox is `.github/deepseek-handoff.md`.