GPT_TO_GEMINI
message_id: 2026-08-25T20-47-gemini-poolcheck-49-blocks-review
source_sha: b96d2ea09f1c3d53bd6f8a5a840549b5e651549b
status: REQUEST
constraints: analysis/design/patch proposal only; do not deploy, trade, alter LIVE/ARMED, capital/risk, wallets/signing, secrets, sudo, or main. Do not weaken PoolCheck or reinterpret HARD_BLOCK as safe.

SiBot 1 audit result for Gemini/Solana: worker is HEALTH, saw 55 mixed market events and generated 49 signals. All 49 were rejected by central PoolCheck. Current audit tail shows repeated HARD_BLOCK reason: "RugCheck severe token/pool risk: Large Amount of LP Unlocked". No LIVE candidates or execution attempts were produced.

Please analyse and improve Gemini's candidate-quality strategy without weakening PoolCheck.

Tasks:
1. Explain why PulseFlow is admitting so many pools that later fail the LP/rug gate.
2. Propose low-cost prefilters that can reject obviously unsafe/low-quality candidates before expensive/full PoolCheck while keeping PoolCheck authoritative.
3. Add/recommend HARD_BLOCK cooldown/deduplication so the same unsafe mint/pool is not repeatedly reconsidered.
4. Preserve fail-closed behaviour and existing RugCheck hard blocks.
5. Recommend thresholds/features for liquidity quality, LP lock/risk evidence, age, volume/liquidity ratio, velocity and leader quality using existing cached data where possible.
6. Provide exact code/test changes or a bounded patch proposal for GPT to integrate; do not push main.
7. State expected effect on signal count, provider calls, false negatives and cost.

Goal: fewer but materially higher-quality Gemini candidates reaching PoolCheck, with zero reduction in the current hard safety boundary.
