requested: 2026-08-24T23:12+01:00
purpose: deploy AUTOTRADE persistence-race fix with serialized CSV writes and read-back verification
source_pr: 624
fix_merge: 755dcbad1e086143be68bcc41b15d312322024ef
constraints: exact current main only; restricted deploy wrapper; full repository pytest green; preserve global OFF hard-kill, chain-specific narrowing, wallet/private-key isolation, LIVE/ARMED controls, pool-rug gates, capital, slippage, minimum-profit protection and final pre-broadcast eth_call
