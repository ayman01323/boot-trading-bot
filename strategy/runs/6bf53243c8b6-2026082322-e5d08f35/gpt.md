# GPT strategy review

Architecture-only review completed. The repository has useful fail-closed execution controls and money-weighted strategy evaluation, but Strategy Lab net P&L does not structurally require gas, priority fees, price impact, failed-attempt costs, or user profit fees. Cross-chain SHADOW features likewise omit explicit chain gas/priority costs. Consequently, strategy/market losses cannot yet be reliably separated from execution/infrastructure failures in promotion evidence. No profitability, CANARY-readiness, or LIVE-readiness claim is supported.

## IMPROVE — Strategy Lab portfolio evaluation
Promotion decisions must use durable money-weighted user NET P&L after every economic cost. Optional caller-supplied net values are insufficient because incomplete cost attribution can make a losing strategy appear positive.

## REWORK — Cross-chain executable-edge strategies
Solana priority fees and EVM gas are fixed or state-dependent native-token costs whose basis-point burden changes with notional. A common 3-6 bps edge floor can be positive while executable user net is negative, especially at low capital.

## SHADOW_MORE — Solana and EVM leader-copy and market-native strategies
A negative return after a correctly executed signal is STRATEGY/MARKET loss. Quote failure, stale detection, simulation or submission failure, abnormal inclusion delay, unexpected fee, reconciliation failure, or inability to exit is EXECUTION/INFRASTRUCTURE failure. Counts alone cannot show their money-weighted effects.
