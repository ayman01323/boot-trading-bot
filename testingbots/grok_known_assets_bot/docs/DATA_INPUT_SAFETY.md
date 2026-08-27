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

For a non-native asset, Jupiter evidence must also identify every route pool that directly touches the target asset (`asset_pool_ids`). Every such pool must be present in the fresh Pool Capital-Safety evidence's `approved_pool_ids`. A route with missing target-asset pool identifiers, missing safety coverage, or any uncovered target-asset pool fails closed before a strategy snapshot is produced.

## Pool capital-safety evidence

Non-native tokens require fresh pool-safety evidence before a normalized snapshot is produced. The safety adapter uses an explicit `passed` result from the upstream Pool Capital-Safety/RugCheck policy rather than guessing whether a vendor-specific numeric score is high-good or low-good.

The pool-safety evidence also carries the exact approved target-asset pool IDs used to bind current Jupiter routes to the safety decision. Native assets can omit RugCheck evidence.

## Units and PAPER cost accounting

Field names are the contract:

- `_bps`: basis points
- `_pct`: percentage points
- `_usd`: US dollars
- `_ms`: milliseconds since Unix epoch or elapsed milliseconds as named

The entry-gate round-trip cost estimate is:

`spread + 2*(fee + impact + slippage)`

all in basis points, converted to percentage points exactly once.

PAPER realised PnL does not charge spread a second time because the entry ask and exit reverse bid already contain the bid/ask difference. Instead it stores the entry-side `fee + impact + slippage` when the PAPER position opens, allocates that entry cost pro-rata across partial exits, and charges the current exit-side `fee + impact + slippage` on each close. Exit-trigger net return uses the same entry-plus-exit route-cost basis. This keeps qualification estimates and PAPER accounting comparable without double-counting spread.

## Persistent breaker semantics

The SQLite journal persists one UTC day-start equity baseline per calendar day.

- On the first evaluation seen for a UTC day, that equity becomes the day's stored baseline.
- A mid-day PAPER process restart reloads the already stored baseline; it does not replace it with current equity.
- If the process remains running across UTC midnight, `StrategyEngine` detects the new UTC day and loads/creates that new day's baseline using the first equity observed after rollover.
- The previous day's baseline is never reused for the new day.

Consecutive losses are counted by completed `TRADE_RESULT` events, not partial `CLOSE` events. TP1 partial exits therefore do not count as separate wins/losses. Partial PnL is accumulated until the trade is fully closed, when one completed trade result is emitted.

## ARMED-PAPER acceptance

Before a real provider collector is connected:

1. All feed-safety and breaker tests must pass.
2. No symbol-only or wrong-address record may produce a validated snapshot.
3. Stale or conflicting providers must fail closed.
4. Jupiter forward and reverse evidence must be present and fresh.
5. Non-native tokens must pass pool-safety validation and every Jupiter target-asset route pool must be safety-approved.
6. Market-data and safety-evidence ages must remain separate.
7. Fee, spread, impact and slippage units must produce deterministic cost results; PAPER PnL must charge both entry and exit route costs without charging spread twice.
8. Mid-day restart and UTC-day rollover breaker semantics must pass tests.
9. PAPER startup/execution boundaries remain unchanged; no live execution adapter may be introduced by this layer.
