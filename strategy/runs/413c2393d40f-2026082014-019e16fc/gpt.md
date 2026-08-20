# GPT strategy review

Architecture-only review completed. The repository contains fail-closed executable-edge gates and chain-specific safeguards, but no fresh runtime forensics support profitability, CANARY readiness, or LIVE readiness. Solana Strategy Lab currently records leader observations with zero executable edge and no outcomes; EVM shadow evidence is quote/simulation-only. Preserve abstention and classify negative realised net returns after complete execution as STRATEGY/MARKET losses, while RPC, simulation, broadcast, confirmation, receipt, or settlement failures remain EXECUTION/INFRASTRUCTURE failures.

## IMPROVE — Cross-chain Strategy Lab outcome attribution
Strategy selection cannot be assessed using quote wins or trade counts. Shadow observations need delayed, money-weighted realised NET outcomes and explicit failure attribution. A completed trade with adverse price movement is STRATEGY/MARKET loss; inability to simulate, broadcast, confirm, reconcile, or exit is EXECUTION/INFRASTRUCTURE failure and must not be silently treated as a market loss or omitted.

## NEW_SHADOW — Solana executable copy-edge measurement
Historical leader returns cannot establish current executable copy edge. Solana requires a current round-trip or marked-exit hypothesis incorporating copying delay, Jupiter route economics, priority/Jito costs, account rent treatment, sellability, and transaction failure probability.

## SHADOW_MORE — EVM cross-venue and learned-route strategies
The preflight architecture is conservative, but fixed basis-point thresholds are unsupported without observed quote-to-inclusion deterioration, revert probability, failed-attempt gas, and chain-specific fee volatility. Positive quoted edge alone is insufficient.
