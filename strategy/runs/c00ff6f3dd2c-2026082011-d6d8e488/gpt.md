# GPT strategy review

Architecture-only review completed. The repository generally fails closed, keeps SHADOW evaluation non-signing, requires positive estimated edge, and explicitly prevents quote simulations from serving as promotion evidence. However, Strategy Lab accounting does not structurally capture every chain-specific cost and does not separate STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. Solana SHADOW observations intentionally lack contemporaneous executable quotes, while EVM SHADOW outcomes are simulations rather than realised fills. Profitability, CANARY readiness and LIVE readiness therefore cannot be claimed.

## REWORK — Strategy Lab evaluation and attribution
Durable money-weighted NET P&L requires immutable, reconcilable cost components and mutually exclusive outcome attribution. An adverse filled trade after complete costs is STRATEGY/MARKET loss; simulation, RPC, stale-blockhash, revert, dropped transaction, quote mismatch or missing confirmation is EXECUTION/INFRASTRUCTURE failure. Failed attempts may still consume gas or priority fees and must reduce net P&L without being mislabeled as market losses.

## NEW_SHADOW — Solana contemporaneous executable-edge adapter
Historical leader performance is not an executable Solana signal. A SHADOW-only adapter should obtain contemporaneous round-trip quotes at intended size, signed-transaction simulation results, price impact, platform/swap fees, priority fees, account effects, quote age and sellability before estimating net edge.

## SHADOW_MORE — EVM net-edge strategies
The EVM gates are economically sensible, but simulation cannot establish durable returns under state contention, MEV, inclusion delay, base-fee movement and failed-attempt costs. These effects must be calibrated per chain and route before promotion.
