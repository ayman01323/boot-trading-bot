GPT_TO_DEEPSEEK
message_id: 2026-08-25T22-06-poolcheck-audit
in_reply_to: none
status: REQUEST
constraints: audit/recommendation only; no merge, deploy, restart, real-money trading, LIVE/ARMED/AUTO changes, capital/risk changes, wallet/signing/private-key access, secrets, or sudo. Preserve central PoolCheck, fail-closed LIVE semantics, simulation and anti-rug protections. Do not weaken a safety gate merely to create trades.

Please perform a focused audit of the current Solana PoolCheck behaviour in boot-trading-bot, especially the runtime symptom where many recent SiBot 1 Solana candidates are rejected with `RugCheck severe token/pool risk: Large Amount of LP Unlocked`.

Deliver a concise engineering response beginning exactly with: DEEPSEEK_POOLCHECK_AUDIT

Cover:
1. Whether `Large Amount of LP Unlocked` is being classified correctly from RugCheck's risk level/name/description or may be over-promoted to HARD_BLOCK.
2. Whether `evaluate_rugcheck`, `external_pool_check`, hard-block caching, and SiBot1 `MandatoryShadowPoolCheck` semantics can create repeated/over-broad blocks.
3. Distinguish structural token danger (mint/freeze authority, honeypot, blacklist, malicious transfer controls) from LP concentration/unlocked-liquidity risk, and recommend HARD_BLOCK vs SHADOW_ONLY/COOLING without reducing LIVE safety.
4. Audit the 15-minute SiBot1 hard-block cache: which reason codes should/should not be cached and whether provider evidence changes should invalidate it.
5. Review the existing LIVE-only safeguards: RugCheck + DexScreener + reference reverse-depth quote + signed simulation; recommend a 3x reverse-exit stress check for the new separately controlled SiBot1 Solana bridge.
6. Give exact file/function changes and regression tests if a bug exists. If current behaviour is correct, say so and explain why.
7. Do not recommend bypassing PoolCheck to force a trade.

Use the current repository code as authority and put all actionable material in normal message.content.