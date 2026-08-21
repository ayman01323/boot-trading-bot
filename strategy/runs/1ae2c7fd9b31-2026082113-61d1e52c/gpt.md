# GPT strategy review

Architecture-only review completed. The repository generally requires positive executable edge and preserves simulation, liquidity, sellability and chain-specific execution protections, but current Strategy Lab accounting can overstate user-retained net P&L by recording pre-profit-share results. EVM candidate selection also stops at the first passing simulation rather than selecting the best retained net edge. Solana has detailed fee, priority-fee and refundable-rent controls, but its common SHADOW adapter deliberately lacks executable outcome evidence. Monetary STRATEGY/MARKET losses must be evaluated separately from EXECUTION/INFRASTRUCTURE failures; the current aggregate failure counter is insufficient for that attribution. Runtime forensics are unavailable, so no strategy is shown profitable, canary-ready or live-ready.

## REWORK — Strategy Lab realised P&L accounting
The objective is durable user-retained net P&L after every economic cost. Promotion and throttling decisions based on pre-profit-share P&L can misclassify marginal strategies as profitable.

## IMPROVE — EVM direct-market route selection
Selecting the first valid trade can leave higher executable net edge unused. Ranking must remain chain-local and use wallet-specific retained net value, not raw win probability or gross profit.

## NEW_SHADOW — Solana executable-edge outcome adapter
Historical leader profitability is not the follower's executable edge. Solana promotion evidence requires contemporaneous Jupiter round-trip quotes and later receipt-reconciled outcomes including priority fees, impact, latency and rent treatment.

## IMPROVE — Cross-chain strategy attribution
A valid signal that loses after an adverse market move is a STRATEGY/MARKET loss. RPC failure, stale quote, simulation failure, revert, receipt timeout, reconciliation gap or worker outage is an EXECUTION/INFRASTRUCTURE failure. Combining them causes incorrect strategy replacement and hides operational leakage.

## SHADOW_MORE — Cross Venue Net Arbitrage; Liquidity Confirmed Momentum; Dislocation Mean Reversion; Flow Acceleration; New Liquidity Quality; Learned Route Replication; Forecasted Positive Net Edge
The strategies correctly abstain when executable edge or quality evidence is missing, but architecture and simulated quotes cannot establish durable profitability.
