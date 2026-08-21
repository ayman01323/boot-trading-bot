# GPT strategy review

Architecture-only review completed. The common strategy layer correctly requires positive estimated net edge and fail-closed liquidity, sellability and quote freshness. EVM has exact-quote and simulation plumbing; Solana Strategy Lab deliberately lacks a current executable-edge adapter and therefore abstains. No fresh runtime forensics exist, so profitability and CANARY/LIVE readiness cannot be claimed. Promotion logic based on very small realised samples should be reworked in SHADOW research before any future promotion.

## REWORK — Cross-strategy promotion evidence
A few favourable trades can promote a strategy despite unstable capital-weighted returns, tail losses or paid failed executions. Strategy/market losses must be measured as completed-position net outcomes, while execution/infrastructure failures must be separately labelled and their irreversible costs still charged to economic P&L.

## NEW_SHADOW — Solana current executable-edge adapter
Historical leader performance is not executable edge. Solana needs chain-specific shadow economics using size-specific entry and exit quotes, route price impact, priority fee, base fee, token-account rent treatment, latency decay and sellability. Until then, abstention is correct.

## SHADOW_MORE — EVM net-edge strategies
A fixed 4-6 bps margin may be consumed by quote decay, inclusion uncertainty, adverse selection or paid failures. Simulation rejection is an infrastructure event with no strategy loss, whereas a successfully executed negative-net cycle is a strategy/market loss; a reverted landed transaction is an execution failure whose gas still reduces net P&L.

## KEEP — Common executable-edge safeguards
These safeguards align with abstention when executable edge is absent. They should remain mandatory while cost inputs and runtime outcomes are validated.
