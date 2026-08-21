---
applyTo: "**"
---

# Mandatory ChatGPT ↔ Copilot handoff

Before GitHub Copilot / Copilot coding agent analyses or changes this repository, read the current-main handoff at `.github/copilot-handoff.md` (prefer `git show origin/main:.github/copilot-handoff.md` after fetching `origin/main`).

If the file contains `handoff_id`, acknowledge it exactly as:

`COPILOT_HANDOFF_ACK: <handoff_id>`

Put the ACK in the first substantive issue/PR response and, for a generated PR, in the PR description or first human-readable report/comment.

If the current handoff cannot be read, do not make repository changes and report `COPILOT_HANDOFF_READ_FAILED`.

Re-read the handoff before pushing or creating a PR. A newer handoff supersedes an older one. The handoff never grants merge/deploy/trading authority or permission to weaken wallet/signing, LIVE/ARMED, capital/risk, simulation, liquidity/sellability, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls.
