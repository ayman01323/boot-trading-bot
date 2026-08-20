# GPT strategy review

Architecture-only review completed. EVM atomic-cycle execution has strong fail-closed quote, gas-reserve and immediate pre-broadcast simulation controls. Cross-chain SHADOW rules require positive cost-adjusted edge, liquidity and sellability. However, Strategy Lab accounting does not explicitly classify STRATEGY/MARKET losses versus EXECUTION/INFRASTRUCTURE failures or require failed-transaction costs in net P&L. Solana copy SHADOW accounting also relies on static fee estimates, which may misstate chain-specific net performance. With runtime forensics missing, no profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## IMPROVE — Cross-chain Strategy Lab accounting
A strategy can have sound signals yet lose through RPC, signing, landing or reconciliation failures. Conversely, a correctly executed trade can lose because the market moved. These outcomes require separate attribution while every irreversible cost still reduces money-weighted net P&L.

## SHADOW_MORE — Solana leader-copy strategy
Solana priority fees, route costs, refundable account rent and landing failure rates are regime-dependent. Static fee estimates can overstate executable edge even when quoted sellability is acceptable.

## KEEP — EVM single-router atomic cycle
The architecture appropriately avoids treating discovery-stage gross edge as executable profit and uses atomic output protection. This should remain unchanged pending fresh realized execution evidence.

## IMPROVE — Strategy promotion evaluation
Positive aggregate net can be concentrated in one regime or small notionals. Durable promotion requires unseen-window performance, complete costs and money-weighted results across realistic capital sizes.
