requested: 2026-08-25T06:43+01:00
purpose: deploy BotBuc backup alert hardening so failure warnings are emitted no more than once per hour and include the recorded failure reason
source_pr: 627
fix_merge: 52a56e8b735882868a6bc84e92d9833cbd7a047e
constraints: exact current main only; restricted deploy wrapper; full repository pytest green; automatic restore on failure; do not change trading, wallet/private-key isolation, LIVE/ARMED controls, pool-rug gates, capital, slippage, minimum-profit protection or transaction broadcast behaviour
