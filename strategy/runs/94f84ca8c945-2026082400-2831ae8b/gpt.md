# GPT strategy review

COPILOT_HANDOFF_ACK: 2026-08-21T21-52Z-copilot-protocol. Architecture-only review completed at the requested commit and exact evidence hash. The design generally prioritises net results, abstention, simulation and chain-specific execution controls. However, EVM scanner eligibility does not require the emitted sellability or atomic-profit-protection fields, Strategy Lab can promote cost-incomplete aggregate records, and failure attribution remains too coarse for reliable strategy decisions. Runtime evidence is unavailable, so no profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## REWORK — EVM direct-market atomic cycle selection
Treat missing or false sellability and atomicity evidence as ineligible during SHADOW evaluation. EVM gas, revert risk and multi-step exposure must not be conflated with quoted gross spread.

## IMPROVE — Strategy Lab lifecycle evaluation
Money-weighted promotion decisions are only meaningful when every material cost and failed execution is reconciled and results are not dominated by one chain or in-sample window.

## SHADOW_MORE — Cross-chain strategy outcome attribution
STRATEGY/MARKET loss must be distinguished from EXECUTION/INFRASTRUCTURE failure before thresholds or signals are changed. Failed attempts can consume fees even when no trade is counted.

## SHADOW_MORE — Solana copied-leader positive executable edge
The Solana design is conservative, but fixed fee and latency reserves plus a separate configured fee-margin assumption require calibration against matched realised outcomes.
