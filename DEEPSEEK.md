# DeepSeek repository instructions

## Mandatory AI identity in GitHub-visible output

Follow `docs/AI_AGENT_IDENTITY.md`. Every DeepSeek-authored GitHub comment, issue body, PR body and human-readable report must begin with:

`🔴 AGENT: DEEPSEEK`

AI-created PR titles must start with `[DEEPSEEK]`. When known, include Role, Workflow/Task, Cycle and Source SHA directly below the identity header. AI-originated commit messages should include the trailer `AI-Agent: DEEPSEEK`.

The repository may invoke DeepSeek through a Claude-compatible endpoint or Claude Code as a read-only harness. That transport does **not** make the provider Claude. When the model/provider is DeepSeek, always identify as DEEPSEEK and never use the Claude identity header.

## Safety and audit boundaries

DeepSeek Strategy and Engineering reviews are report-only unless a separate, explicitly authorised implementation path says otherwise. Do not trade, deploy, alter LIVE/ARMED/capital settings, touch wallet/signing material or secrets, or weaken simulation, positive-edge, liquidity, sellability, slippage, nonce, stop-loss, circuit-breaker or reconciliation protections.

For Engineering Audit work, include API/model cost, server bandwidth and disk usage in the operational-efficiency assessment, using only sanitised evidence and without weakening safety controls to save resources.
