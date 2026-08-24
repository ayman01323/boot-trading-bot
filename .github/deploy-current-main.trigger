requested: 2026-08-24T22:22+01:00
purpose: deploy AUTOTRADE global-scope resolution fix so canonical *=true is not overridden by stale legacy chain_id=0=false rows
source_pr: 620
fix_merge: a00e512b7cf7670077816e3160433430204fa5ae
constraints: exact current main only; restricted deploy wrapper; full repository pytest green; automatic restore on failure; preserve wallet/private-key isolation, LIVE/ARMED controls, pool-rug gates, capital, slippage, minimum-profit protection and final pre-broadcast eth_call
