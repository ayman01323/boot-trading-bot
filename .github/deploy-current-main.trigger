requested: 2026-08-25T06:58+01:00
purpose: deploy exact current main with Basic Engine v0 scanner-starvation repair after clearing hung read-only VPS diagnostics
source_pr: 628
queue_safeguard_pr: 630
expected_main: cf1980a84579edb2a17d7e586e5bb776797e8218
constraints: restricted deploy wrapper; cancel noncritical read-only diagnostics only; never interrupt active deployments; preserve LIVE/AUTO/ARMED, wallet isolation, capital caps, quote/liquidity/impact/quarantine/minimum-profit/pool-rug/simulation/final eth_call protections
