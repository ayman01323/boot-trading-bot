# ARMED-PAPER Data Input Safety Contract

This module remains PAPER-only. There is no signer, wallet access, transaction broadcast, order submission or live execution adapter in this design.

## Required real-feed path

Real provider data must not be converted directly into `MarketSnapshot`. The intended path is:

Provider collectors -> canonical `(chain,address)` registry -> `SafeSnapshotBuilder` -> validated snapshot envelope -> deterministic Grok research gate -> host PAPER risk engine.

## Canonical identity

Every real-feed evaluation is keyed by exact `(chain,address)`. Symbols are display metadata only and never authorise a non-native asset. An incoming provider record whose chain/address does not match an enabled canonical asset fails closed.

## Provenance and freshness

Every provider observation carries:

- provider name
- canonical chain/address
- provider source timestamp
- local received timestamp
- optional pool id and block/slot
- the normalized fields supplied by that provider

Freshness is provider-specific. Defaults are intentionally different: Jupiter 5s, Birdeye 10s, DexScreener 20s, Helius/Alchemy 10s, CoinGecko 120s and RugCheck 300s. Unknown providers fall back to the host quote-age limit.

Market-data freshness and pool-safety freshness are tracked separately. A valid 2-minute-old RugCheck result must not make a 1-second-old executable quote look 2 minutes stale.

## Provider disagreement

When two or more providers supply a positive USD price, their max/min midpoint disagreement is measured. The default fail-closed threshold is 1.00%. A disagreement above the configured threshold produces no normalized strategy snapshot.

No averaging is used to manufacture consensus. For each non-execution field the freshest eligible provider is selected and recorded in `field_sources`.

## Jupiter execution evidence

The PAPER qualification path requires normalized Jupiter evidence for both directions:

- forward route exists
- reverse route exists
- positive bid/ask/reverse bid
- valid spread
- impact at or below host limit
- non-negative fees and slippage
- route evidence within the Jupiter freshness TTL

Jupiter supplies the executable bid/ask/reverse bid and execution-cost fields. Display-site prices never override the executable route.

## Pool capital-safety evidence

Non-native tokens require fresh pool-safety evidence before a normalized snapshot is produced. The safety adapter uses an explicit `passed` result from the upstream Pool Capital-Safety/RugCheck policy rather than guessing whether a vendor-specific numeric score is high-good or low-good.

Native assets can omit RugCheck evidence.

## Units

Field names are the contract:

- `_bps`: basis points
- `_pct`: percentage points
- `_usd`: US dollars
- `_ms`: milliseconds since Unix epoch or elapsed milliseconds as named

The host round-trip cost model is:

`spread + 2*fee + 2*impact + 2*slippage`

all in basis points, converted to percentage points exactly once.

## Persistent breaker semantics

The SQLite journal now persists the UTC day-start equity baseline. Restarting the PAPER process cannot silently reset the daily realised-loss breaker baseline.

Consecutive losses are counted by completed `TRADE_RESULT` events, not partial `CLOSE` events. TP1 partial exits therefore do not count as separate wins/losses. Partial PnL is accumulated until the trade is fully closed, when one completed trade result is emitted.

## ARMED-PAPER acceptance

Before a real provider collector is connected:

1. All feed-safety and breaker tests must pass.
2. No symbol-only or wrong-address record may produce a validated snapshot.
3. Stale or conflicting providers must fail closed.
4. Jupiter forward and reverse evidence must be present and fresh.
5. Non-native tokens must pass pool-safety validation.
6. Market-data and safety-evidence ages must remain separate.
7. Fee, spread, impact and slippage units must produce deterministic cost results.
8. PAPER startup/execution boundaries remain unchanged; no live execution adapter may be introduced by this layer.
