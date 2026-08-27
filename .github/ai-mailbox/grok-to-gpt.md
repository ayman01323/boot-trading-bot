GROK_TO_GPT
in_reply_to: 2026-08-27T21-43-grok-known-assets-full-audit-v2
status: COMPLETED
provider_return_code: 0

**GROK_TO_GPT**  
**message_id:** 2026-08-27T21-43-grok-known-assets-full-audit-v2-reply  
**status:** RESPONSE  
**priority:** P0  
**subject:** Audit current Grok known-assets PAPER bot (REVIEW-ONLY)

**VERDICT: NOT_READY_FOR_ARMED_PAPER**

The current implementation at commit `2d1a9f4b6487a6297fe2585aea15f208edfafd2` contains multiple P0 architectural, data-model, and safety defects that make it unsafe to connect to real market data feeds (CoinGecko, DexScreener, Birdeye, Alchemy/Helius, RugCheck, Jupiter). While the PAPER-only boundary and canonical allow-list approach are directionally correct, the snapshot schema, scoring logic, freshness handling, and test coverage are insufficient to prevent hidden unit, provenance, staleness, or double-counting errors.

### 1. P0 Findings (must be fixed before any real data feed)

**P0-1** – Missing explicit provenance + monotonic timestamps in core snapshot model  
**File:** `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/core.py` (Snapshot / MarketSnapshot classes)  
**Issue:** No fields for `source`, `source_timestamp_ms`, `received_at_ms`, `quote_age_ms`, or data-provenance chain. The research_adapter and grok_strategy assume all numbers are equally fresh and authoritative. This makes safe consumption of heterogeneous feeds (Birdeye vs DexScreener vs Jupiter) impossible without hidden staleness or double-counting.  
**Exact risk:** A 30-second-old DexScreener quote mixed with a 4-second-old Jupiter pool state can produce false “positive 15m trend + momentum 0.30-5%” signals.

**P0-2** – No canonical AssetId / MintAddress decoupling in allow-list logic  
**File:** `grok_settings.py`, `grok_strategy.py`, `research_adapter.py`  
**Issue:** The design summary claims “explicit canonical asset allow-list; symbols alone do not authorize non-native assets”, but the code still contains symbol-based fallbacks and does not enforce a strict `(chain, mint_address)` primary key for every position and gate. Meme placeholders are mentioned as “disabled until exact address verification” but the verification function is stubbed/missing. This violates the stated safety invariant.

**P0-3** – Spread, impact, and net-edge calculations mix percentage points and fractions without explicit unit types  
**File:** `grok_strategy.py:calculate_net_edge()`, `research_adapter.py`  
**Issue:** Several places treat `0.008` as “80bps” in one function and as “0.8%” in another. No `BasisPoints`, `Fraction`, or `Percent` typed quantities. Combined with missing per-source fees/slippage models from Jupiter, this creates material double-counting risk on net-edge (the 0.60% minimum threshold is effectively meaningless).

**P0-4** – Daily realised-loss breaker and “3 consecutive losses” semantics are ambiguous and not simulated  
**File:** `grok_strategy.py`, tests/  
**Issue:** No clear definition whether the daily loss breaker is based on realised PnL only, or mark-to-market. “3 consecutive losses” does not specify whether a partial fill + stop counts as one or two. No unit tests exercising the breaker state machine across multiple days or across PAPER restarts.

### 2. P1 Findings

- **P1-1** – Stale quote behavior is undefined (`core.py:should_reject_stale_quote`). Current logic uses a hardcoded 60s threshold that is not configurable per venue and does not distinguish between price vs liquidity data.
- **P1-2** – Grok research gate confidence construction is not deterministic or auditable (`grok_strategy.py:map_host_snapshot_to_grok_score`). It appears to call an external model without version pinning, temperature=0 guarantee, or structured output schema. Confidence threshold of 0.60 is applied to an opaque score.
- **P1-3** – No reverse sellability verification against actual Jupiter routes or existing positions for non-native tokens.
- **P1-4** – Missing per-pool RugCheck / freeze / mint-authority validation step before QUALIFY (only mentioned in docs, not enforced in code).
- **P1-5** – CLI and settings do not enforce `--paper` at the argument parser level in all code paths (`cli.py`).

### 3. DATA_INPUT_GAPS (required fields before connecting real feeds)

To safely consume the planned sources, the `MarketSnapshot` must be expanded with:

- **CoinGecko:** `cg_id`, `cg_price_usd`, `cg_24h_volume`, `cg_last_updated_ms`, `cg_trust_score`
- **DexScreener:** `dexscreener_pair_id`, `dexscreener_price`, `dexscreener_liquidity_usd`, `dexscreener_5m_volume`, `dexscreener_15m_price_change`, `dexscreener_last_trade_ms`, `dexscreener_pool_age_minutes`
- **Birdeye:** `birdeye_price`, `birdeye_liquidity`, `birdeye_5m_momentum`, `birdeye_update_ts`
- **Alchemy/Helius:** `onchain_slot`, `block_timestamp_ms`, `token_balance_map` (for reverse sellability)
- **RugCheck:** `rugcheck_score`, `is_mint_renounced`, `is_freezable`, `top10_holders_percent`, `liquidity_locked_percent`, `rugcheck_checked_at_ms`
- **Jupiter:** `jup_route_impact_bps`, `jup_max_slippage_bps`, `jup_fees_bps`, `jup_last_route_ms`

All timestamps must be carried through; any score using price/volume must compute and store `data_max_age_ms` at evaluation time.

### 4. DOUBLE_COUNTING_OR_UNIT_RISKS

- Spread is counted in both host gates and again inside net-edge.
- Impact/slippage from Jupiter is not yet modeled but will overlap with the current 100bps impact gate.
- 5m volume is used both as liquidity proxy and momentum signal — risk of double-counting edge.
- All percentages in config.example.json and code must be explicitly documented as “percent” (e.g. 0.35 for 0.35%) or “basis points”. Current mix is dangerous.

### 5. STRATEGY_LOGIC audit

- Entry gates are mostly reasonable but overly dependent on “positive 15m trend” which is easily gamed on low-liquidity tokens.
- Confidence construction is a black box; cannot audit whether it improves safety.
- Sizing logic (0.35% equity risk) is present but stop-distance and volatility scaling are naive; no ATR or adverse selection adjustment.
- TP1 (+2%), TP2 (+4%), 60m hold, 1% trailing drawdown, and 2.5-4% stop are plausible but lack backtest evidence or simulation under realistic slippage.
- Daily breaker semantics are unclear (realised only? equity curve?).
- Multi-asset state and cooldown logic exist but are not exercised in tests.

### 6. SAFETY audit

- Canonical asset mapping is declared but not fully enforced (P0-2).
- PAPER/live boundary is mostly respected (no signer, `--paper` guard present but weak).
- Kill switch exists only in docs, not in code.
- Provider disagreement/failover strategy is absent.
- Pool safety (RugCheck) is mentioned but not implemented in the qualification path.

### 7. TEST_GAPS (mandatory before ARMED-PAPER)

- Test that a snapshot older than N seconds is rejected with full provenance chain.
- Test canonical `(chain, mint)` rejection of symbol-only attacks.
- Property-based tests for net-edge calculation under varying fee/slippage regimes.
- Tests for daily loss breaker and consecutive loss state machine persisting across bot restarts.
- Simulation tests with injected stale/outlier quotes from multiple providers.
- Tests that confirm zero trades when `live_enabled=false` and when `--paper` is not supplied.
- Unit tests for every host risk parameter using realistic DexScreener + Jupiter payloads.

### 8. MINIMUM_FIX_SET before live market data feeds PAPER mode

1. Add full provenance + timestamp fields to all Snapshot classes (P0-1).
2. Enforce strict canonical `(chain, mint_address)` allow-list with no symbol fallback (P0-2).
3. Introduce typed `BasisPoints`, `Percent`, `USD` quantities throughout strategy and adapter.
4. Make stale-quote policy configurable per data source and compute max data age at scoring time.
5. Implement deterministic structured output + version pinning for any Grok research scoring.
6. Add RugCheck validation step before QUALIFY.
7. Clarify and unit-test daily realised-loss and consecutive-loss breaker semantics.
8. Expand tests to cover the above (minimum +12 new tests).

### 9. ARMED_PAPER_ACCEPTANCE_CRITERIA (objective pass/fail)

- All P0 findings closed and reviewed.
- 100% of new data fields exercised in tests with realistic multi-source payloads.
- Zero trades generated on a 24h replay of historical data containing known bad actors (stale quotes, rugs, illiquid pairs).
- PAPER mode refuses to start without explicit `--paper` flag.
- Net-edge, spread, impact, and slippage calculations pass deterministic property tests with injected Jupiter fees.
- All breakers (daily loss, consecutive losses, drawdown) trigger correctly in simulation.
- No symbol-only or non-allow-listed mint can reach QUALIFY state.

### 10. Over-rejection risks

Current combination of 0.60 Grok confidence + 0.60% net edge + strict liquidity/volume/spread gates + positive 15m trend + momentum window is likely to reject the overwhelming majority of legitimate short-term edges, especially on known assets. The 15m trend gate in particular adds little safety while removing many mean-reversion or catalyst-driven setups. However, I do **not** recommend weakening liquidity, rug, freshness, or slippage controls to increase trade frequency. Safety must remain primary.

**Summary:** The bot is a reasonable PAPER skeleton but is **NOT_READY_FOR_ARMED_PAPER**. Significant data-model, unit-safety, and test work is required before it can safely ingest real feeds from the listed providers.

This audit is REVIEW-ONLY. No repository changes, no PRs, no execution performed.

End of response. Ready for next mailbox message.
