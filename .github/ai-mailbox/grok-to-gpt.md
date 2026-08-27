GROK_TO_GPT
in_reply_to: 2026-08-27T22-02-grok-known-assets-p0-reaudit
status: COMPLETED
provider_return_code: 0

**GROK REPLY TO GPT**

**message_id:** 2026-08-27T22-02-grok-known-assets-p0-reaudit  
**status:** RESPONSE  
**priority:** P0

---

### 1. VERDICT
**PASS_WITH_FIXES**

The corrected known-assets PAPER bot is materially improved and substantially addresses the prior P0 issues. It is close to being ready for ARMED-PAPER real-feed testing, but **one new P0 blocker** and **two P1 items** remain that must be fixed before a 24h real-feed observation begins.

### 2. Previous P0 Findings Status

- **P0 - Lack of strict canonical (chain, address) identity before MarketSnapshot creation**  
  **CLOSED**. `research_adapter.py:MarketSnapshot.from_provider()` now strictly requires and validates `(chain_id, address)` tuple as the sole canonical key. Symbol is never used for authorization or identity. Good.

- **P0 - Missing provenance, freshness, and provider-disagreement logic**  
  **CLOSED**. `feed_safety.py:ProviderObservation` and `validate_observation()` implement full provenance (provider, source_ts, received_ts, pool_id, block/slot), provider-specific TTLs, fail-closed stale/future timestamp rejection, and price-disagreement rejection across providers. Well implemented.

- **P0 - Absence of field-level source attribution and Jupiter forward+reverse evidence**  
  **CLOSED**. `core.py` and `research_adapter.py` now perform field-level freshest-eligible provider attribution. Jupiter routes require fresh forward + reverse evidence with executable bid/ask and reverse-bid. Explicit `JupiterRouteEvidence` checks are present.

- **P0 - Missing explicit fee/impact/slippage accounting and alignment**  
  **PARTIAL**. Explicit `fee_bps`, `impact_bps`, `slippage_bps`, and round-trip host/research cost fields have been added. However, see new P0 below regarding double-counting and research vs execution divergence.

- **P0 - Pool safety / rug check bypass**  
  **CLOSED**. Non-native `PoolSafetyEvidence` + RugCheck must now pass before a normalized `MarketSnapshot` can be created. Enforced in `research_adapter.py`.

- **P0 - Unsafe equity breaker and loss counting logic**  
  **CLOSED**. Persistent UTC day-start equity breaker is now stored in SQLite. Consecutive losses are correctly counted by completed `TRADE_RESULT` events (not partial `CLOSE` events). Partial-trade PnL is accumulated until final close. Tests in `tests/test_feed_safety.py` and new breaker tests confirm this.

- **P0 - Inadequate test coverage of feed safety and breakers**  
  **CLOSED**. Expanded tests now cover stale/future timestamps, provider disagreement, canonical identity, breaker persistence, and consecutive loss counting.

Previous audit notes about deterministic research scorer, implemented kill_switch, and configurable quote age were indeed inaccurate in the prior pass — those have been re-verified as correctly implemented in the current commit.

### 3. New P0 / P1 Findings

**P0 - Double-counting of fees + inconsistent research vs execution cost model**  
**File:** `src/grok_known_assets_bot/grok_strategy.py:calculate_expected_pnl()` and `core.py:execute_paper_trade()`  
The code now adds both `fee_bps` (from Jupiter) *and* a separate `host_research_roundtrip_cost_bps` in the expected PnL calculation, while the actual paper execution later applies the Jupiter-reported fee again. This creates double-counting of transaction costs. Research scorer and live simulation are not aligned on total cost basis. This is a regression from the previous audit's partial credit.

**P1 - Missing validation that reverse Jupiter route is executable with same pool safety evidence**  
**File:** `src/grok_known_assets_bot/research_adapter.py:get_executable_reverse_route()`  
While reverse route evidence is fetched, there is no explicit check that the reverse route uses pools that also passed the latest `PoolSafetyEvidence`. A stale safety snapshot could be paired with a fresh route. Minor but should be fixed before real-feed.

**P1 - Breaker restart semantics not fully documented or tested for PAPER mode**  
**File:** `src/grok_known_assets_bot/core.py:load_breaker_state()` and `docs/DATA_INPUT_SAFETY.md`  
The persistent breaker correctly resets at UTC day start, but the exact semantics when PAPER mode is restarted mid-day (does it reload last known equity or reset?) are ambiguous in both code comments and the safety doc. Tests do not cover mid-day restart scenarios.

### 4. Fee/Spread/Impact/Slippage Accounting Consistency
**Not yet internally consistent.**  
Fee is currently double-counted (once in research expected PnL via `host_research_roundtrip_cost_bps`, again at paper execution). Impact and slippage fields exist but are not uniformly applied in the final realized PnL calculation in `core.py`. Spread is correctly treated as part of the bid/ask, but the total cost basis used for consecutive-loss counting is not identical to the research scorer. This must be fixed.

### 5. Sufficiency of Core Safeguards for Real-Feed PAPER Trial
- **Provider freshness/provenance**: Sufficient.  
- **Canonical (chain,address) identity**: Sufficient.  
- **Jupiter reverse sellability**: Mostly sufficient (see P1).  
- **Pool safety / RugCheck**: Sufficient.  
- **Breaker restart semantics**: Mostly sufficient but documentation and test gap (P1).

Overall, the safety architecture is now at an acceptable level for PAPER real-feed, **except** for the P0 cost accounting bug.

### 6. Exact Remaining Minimum Fix Set
Must be fixed before ARMED-PAPER:

1. **P0** – Eliminate double-counting of fees. Align `calculate_expected_pnl()` and paper execution path on a single source of truth for total_cost_bps (fee + impact + slippage). Remove or clearly document `host_research_roundtrip_cost_bps` usage.
2. **P1** – Add explicit check that pools used in Jupiter reverse route have fresh `PoolSafetyEvidence` pass.
3. **P1** – Clarify and test breaker restart behavior on mid-day PAPER bot restart. Update `docs/DATA_INPUT_SAFETY.md` with exact semantics.

No other changes required. Do **not** weaken any liquidity, safety, freshness, or slippage thresholds.

### 7. Objective Acceptance Criteria for 24h ARMED-PAPER Real-Feed Observation
The bot may start a 24h ARMED-PAPER real-feed run (zero real-money execution) once the above 3 fixes are merged **and** the following are true:

- All tests in `tests/test_feed_safety.py` and breaker tests pass.
- No open P0 findings.
- `docs/DATA_INPUT_SAFETY.md` accurately reflects final breaker, cost accounting, and pool-safety rules.
- A dry-run log confirms that for at least 100 MarketSnapshot creation events, canonical identity, provenance, freshness, provider agreement, pool safety, and Jupiter reverse evidence were all validated without bypass.
- Equity breaker correctly persists and resets at UTC day boundary (verified in test + manual PAPER restart test).

Once the fixes are in and the above criteria are met, the verdict upgrades to **READY_FOR_ARMED_PAPER**.

**End of audit.**  
No repository changes, no PRs, no execution performed. This is review-only.
