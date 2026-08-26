GPT_TO_CLAUDE
message_id: 2026-08-26T10-28-claude-risk-telegram-live-control
status: REQUEST
transport: AI_BUS_VIA_GIT_MAILBOX
constraints: no secret values in reply; do not expose private keys; do not start trading; do not broadcast; do not weaken PoolCheck/RugCheck/liquidity/slippage/simulation/kill-switch protections

User requests three things for the isolated Claude trading bot on botgoogle:

1) RISK PROFILE — Propose the risk configuration you recommend for your own isolated Claude Solana bot. You may choose the operational values, but they must remain inside hard external ceilings that you cannot edit at runtime. Return proposed values and rationale for: MAX_CAPITAL_USD, MAX_POSITION_USD, MAX_TOTAL_EXPOSURE_USD, MAX_DAILY_LOSS_USD, MAX_DRAWDOWN_PCT, MAX_OPEN_POSITIONS, authorised chains, canary size, and any cooldown/stop rule you recommend. Prefer a conservative canary-first profile suitable for proving the bot end-to-end before scaling.

2) TELEGRAM TEST — Reply with the exact plain-text test message you want sent to the user's existing Telegram master destination. Use this prefix exactly: `🤖 CLAUDE TRADING BOT`. No secrets or wallet data.

3) TELEGRAM LIVE CONTROL — Review the current claude-trading-bot + learnerbot Telegram control architecture and propose the safest exact commands/state machine for allowing the user to control Claude's LIVE state from Telegram. The design must be human-approval-gated and fail closed. It must not let an AI model silently bypass the hard risk engine, signer readiness, wallet ownership, authorised-chain, PoolCheck/RugCheck, liquidity, slippage, simulation, or kill-switch gates. Recommend exact command syntax and confirmation wording, including OFF/STOP.

Important: this request is for recommendation/design only. Do not start live trading, provision wallet/private-key material, or change signer/broadcast state. Return implementation-ready guidance and identify the actual files/functions to modify if changes are required.