DeepSeek GitHub-to-VPS Solana no-trade diagnostic trigger
requested: 2026-08-24T00:32:22Z
mode: inspect-only
focus: determine why Solana is not trading now. Inspect current learnerbot.service status/log tail and report, without secrets or wallet/private-key access: effective Solana LIVE state for active MASTER/user if exposed by status logs; solana enabled/live/signing readiness summary; selected leader count; candidate pool/qualified/selected counts; recent Solana BUY/SKIP/REJECT reasons; fresh leader signal availability; position-cap/circuit-breaker state; quote/preflight/simulation/liquidity failures; reserve/capital blockers; websocket/RPC health indicators; and the single dominant current blocker. Do not mutate trading state, thresholds, wallets, LIVE/risk gates, or deploy.
