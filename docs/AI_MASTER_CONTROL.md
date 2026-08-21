# Telegram-selected resilient AI MASTER

MASTER Telegram accounts can control the AI review orchestration without changing trading safety gates.

## Providers

Independent agents:
- GPT
- Claude
- Gemini
- Copilot

The Strategy and Engineering lanes each have an independently selectable preferred MASTER.

Fallback order is fixed:

`selected MASTER -> GPT -> Claude -> Gemini -> other available agent`

The selected provider is never retried after it fails. If the selected provider is already GPT, Claude, or Gemini, that provider is skipped when the fallback chain reaches it.

## Resilience

One valid independent report is sufficient for a review/adjudication cycle to continue. One, two, or three unavailable agents do not stop the AI lane and do not stop the trading engine.

Single-agent decisions use stricter policy:
- Strategy: at least 0.95 confidence, LOW risk only, SHADOW-only, allow-listed files, explicit tests.
- Engineering: at least 0.95 confidence and deterministic evidence for any automated draft fix, plus bounded files/tests.

AI health never disables LIVE trading. Trading continues to depend on the existing wallet, signing, quote, simulation, liquidity/sellability, slippage/impact, capital/reserve, stop-loss, circuit-breaker, nonce and execution-reconciliation protections.

No AI agent or MASTER is authorised by this system to bypass those controls or directly submit a trade merely because other AI agents are unavailable.

## Telegram MASTER menu

Open `AI Reports & Control` -> `AI Master Control`.

For each lane you can:
- choose AUTO, GPT, Claude, Gemini, or Copilot as preferred MASTER;
- enable/disable the AI review lane;
- choose scheduled/manual review mode;
- request Strategy now;
- request Engineering now;
- request both now.

Control changes are written atomically on the VPS and copied to `/var/tmp/boot/ai_master_control.json`. The trading process receives no GitHub credential. A self-hosted GitHub Actions publisher validates and publishes only the sanitised control fields to the `ai-reviews` branch.

## Claude VPS controlled access

When Claude is selected as Strategy or Engineering MASTER, Telegram MASTER accounts also have `AI Reports & Control -> Claude VPS Access`.

Allowed actions are intentionally narrow:
- `Inspect VPS`: reads the existing restricted BOOT status wrapper and gives Claude a redacted operational snapshot.
- `Run VPS tests`: creates an isolated temporary virtual environment from the current GitHub `main` checkout and runs the full pytest suite. It does not modify the production worktree.
- `Deploy current main`: requires a second Telegram confirmation. The workflow fetches `origin/main`, proves the checked-out SHA exactly equals current `origin/main`, then calls only `/usr/local/sbin/deploy-boot-trading-bot <current-main-sha>`. The existing restricted wrapper remains responsible for full server tests and restart-on-success.

Claude is invoked on the self-hosted `boot-vps` runner in plan mode against a sanitised context. Sensitive-looking lines and API/token patterns are redacted before the context is sent to Claude. Results are published to `ai-reviews/vps/claude/latest.json` and shown in the Telegram VPS submenu.

The VPS workflow does not create a shell account for Claude and does not provide SSH credentials. It does not permit:
- a root shell;
- arbitrary `sudo`;
- an arbitrary deploy SHA;
- wallet/private-key, seed, mnemonic, or signing-secret access;
- modification of LIVE risk/safety gates merely because Claude is MASTER.

The only sudo commands present in the Claude VPS workflow are the existing restricted BOOT status and deploy wrappers.

## Failure reporting

Telegram MASTER chats receive AI health warnings showing GPT, Claude, Gemini, and Copilot individually. The message records unavailable agents and the actual MASTER that took over. Warnings state explicitly that AI failure does not disable the trading engine.
