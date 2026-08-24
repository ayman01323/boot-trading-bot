requested: 2026-08-24T22:48+01:00
purpose: deploy AUTOTRADE OFF all-chain hard-kill fix so user global OFF cannot be overridden by stale per-chain true rows, while global ON can still be narrowed by intentional per-chain OFF
source_pr: 622
fix_merge: 8f2bce7dd44c3605ae8c7a248eafcc0293be95b1
constraints: exact current main only; restricted deploy wrapper; full repository pytest green; automatic restore on failure; preserve wallet/private-key isolation, LIVE/ARMED controls, pool-rug gates, capital, slippage, minimum-profit protection and final pre-broadcast eth_call
