# GPT strategy review

Architecture-only review completed. Both chains fail closed on several missing executability inputs, and Solana SHADOW deliberately refuses to infer current edge from leader history. However, Strategy Lab accounting does not fully enforce reconciled all-in money-weighted net P&L, and promotion evaluation does not distinguish STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. EVM realised_net excludes platform-fee settlement and its gas; Solana has richer fee, priority-tip and refundable-rent accounting but lacks fresh runtime validation. No profitability, CANARY-readiness or LIVE-readiness conclusion is permitted.

## REWORK — Strategy Lab cost and outcome accounting
Durable selection requires comparable capital-weighted net outcomes after every irreversible cost. A caller-supplied or partially net figure can promote strategies whose apparent edge is consumed by platform settlement, failure spend or chain-specific overhead.

## IMPROVE — Outcome attribution and promotion governance
A negative post-confirmation market outcome is a STRATEGY/MARKET loss. RPC timeouts, quote expiry, simulation errors, dropped transactions and signer faults are EXECUTION/INFRASTRUCTURE failures; paid reverts additionally reduce economic net. These must remain separate while both affect deployability.

## SHADOW_MORE — Cross-chain executable-edge calibration
EVM edge must cover notional-dependent gas, builder costs, reverts and settlement overhead. Solana edge must cover priority/tip dynamics, route impact, account/rent effects and copy latency. Static common floors cannot establish durable executable edge.
