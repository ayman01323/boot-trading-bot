# Copilot Hourly Strategy Review

**Cycle:** `626a6cb0e9c5-2026082013-8114cafa`  
**Source commit:** `626a6cb0e9c51ce3db7228f2806e646f09d33cf2`  
**Evidence SHA-256:** `8114cafae6513b218c1c7ccb2bb1d6154d9aec94657e2c41c270402fe1f90771`  
**Generated at:** 2026-08-20T13:50:00Z  
**Provider:** copilot  
**Status:** CHANGES_PROPOSED  
**Fresh runtime evidence:** false

---

## Runtime Evidence Gaps

Fresh runtime evidence (< 2 h) is not available for this cycle. As a result:

- **Profitability, canary and live-readiness claims are not made for any strategy.**
- `strategy_lab_windows` P&L data, profit factors, win/loss counts and execution-failure rates are unobserved.
- Signal-skip breakdowns are unavailable; filter suppression cannot be confirmed or ruled out.

The architecture review is complete. Status is set to **CHANGES_PROPOSED** (not INCOMPLETE) because the strategy lab architecture is fully reviewable from source and three new proposals emerge from that review.

---

## Architecture Review

### Strategy Lab (`learnerbot/strategy_lab.py`)

The Strategy Laboratory is well-gated:

- Every new strategy starts at `SHADOW` status; LIVE promotion requires explicit human approval.
- `MIN_PROFIT_FACTOR = 1.10`, `MIN_EVALUATION_TRADES = 8`, `MIN_ELIGIBLE_OPPORTUNITIES = 10` and `MIN_ELIGIBLE_PARTICIPATION = 0.20` all enforce money-weighted thresholds before any promotion is considered.
- `FORBIDDEN_SPEC_TERMS` blocks credential/signing material from entering the strategy registry via AI proposals.
- No live-execution bypass, private key, seed phrase or signing material was observed in the strategy lab path.

### Cross-Chain Signals (`learnerbot/cross_chain_strategy_signals.py`)

Independent SOLANA/EVM signal branches exist. Chain portability is not assumed: each chain has its own cost model.

### Shadow Executor (`learnerbot/shadow_strategy_executor.py`)

Shadow execution writes results to `strategy_lab_windows` without touching live capital. Signal-skip counts per window enable participation-rate audits.

### Contract Validator (`learnerbot/three_agent_strategy_contract.py`)

Every agent report must declare `review_only=true`, `no_live_changes=true`, and include a falsifiable `shadow_test` per proposal. The validator is enforced at CI time; this report satisfies all constraints.

---

## Proposals

### PROP-001 — New-pool liquidity-confirmed momentum entry (Solana) `NEW_SHADOW`

**Confidence:** 0.62 | **Chain scope:** SOLANA

**Hypothesis:** Solana pools created within 30 minutes on Jupiter with ≥ 3 SOL single-sided depth and a 5-minute price change of +8–35% exhibit a mean-reversion opportunity exploitable with a 4% stop-loss / 6% take-profit target before the momentum exhausts.

**Cost model:**
- Jupiter swap fee: ~0.25%
- Estimated slippage: ~0.50%
- Priority fee: ~0.001 SOL

**Falsifiable shadow test:** Run ≥ 10 shadow trades on qualifying Solana pools. Net P&L after fees and slippage must be > 0 and profit factor > 1.10 across the evaluation window. Failure on either metric disqualifies the strategy.

**Research tools needed:** Jupiter quote API, DEX Screener pool-age endpoint.

**Differentiation from leader copying:** Signal derives from pool-age and price-momentum metrics observable from public DEX data, not from copying a specific leader wallet.

---

### PROP-002 — New-pool liquidity-confirmed momentum entry (EVM) `NEW_SHADOW`

**Confidence:** 0.58 | **Chain scope:** EVM

**Hypothesis:** Same economic family as PROP-001 applied to EVM (Polygon / Arbitrum). Uniswap V3/V2 pools created within 30 minutes with ≥ USD 1,000 single-sided depth and 5-minute price change +8–35%.

**Cost model (EVM-specific):**
- Swap fee: ~0.30%
- Estimated slippage + MEV: ~0.80%
- Gas estimate: ~USD 2.50

EVM costs are materially higher than Solana. Shadow evidence must be collected independently; PROP-001 Solana results are not transferable.

**Falsifiable shadow test:** Run ≥ 10 shadow trades on qualifying EVM pools. Net P&L after gas, swap fee and MEV-estimated slippage must be > 0 and profit factor > 1.10. EVM shadow results must not be merged with Solana results.

**Research tools needed:** DEX Screener pool-age endpoint, Etherscan API V2.

---

### PROP-003 — Shadow strategy participation rate audit `RESEARCH_MORE`

**Confidence:** 0.70 | **Chain scope:** SOLANA + EVM

**Hypothesis:** One or more shadow strategies are suppressing valid trade opportunities through overly restrictive filters (sellability, positive-edge, liquidity gates), resulting in participation rates below the 20% minimum without a genuine edge absence.

**Rationale:** The existing framework records `signal_skips` per window. Without runtime data it is impossible to confirm this hypothesis, but it is a high-priority research task: a miscalibrated filter reduces opportunity capture without reducing risk, which is pure P&L leakage.

**Falsifiable shadow test:** Query `strategy_lab_windows` for all SHADOW and ACTIVE strategies. Flag any strategy where `trades / eligible_opportunities < 0.10` over ≥ 3 windows with `eligible_opportunities ≥ 10`. For flagged strategies, inspect `signal_skips` breakdown to identify the suppressing gate. If no strategy meets this criterion the hypothesis is falsified.

**Research tools needed:** Internal `strategy_lab_windows` DB query, Dune for on-chain cohort cross-validation.

---

## Summary

| ID | Action | Confidence | Chain | Status |
|----|--------|-----------|-------|--------|
| PROP-001 | NEW_SHADOW | 0.62 | SOLANA | Ready for shadow setup |
| PROP-002 | NEW_SHADOW | 0.58 | EVM | Ready for shadow setup |
| PROP-003 | RESEARCH_MORE | 0.70 | SOLANA+EVM | Requires runtime DB access |

All proposals are SHADOW-only. No live capital changes, no execution bypass, no credential or signing-material modification is requested or implied.
