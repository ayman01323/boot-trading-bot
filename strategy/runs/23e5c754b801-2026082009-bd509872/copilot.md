# Copilot Hourly Strategy Review

**Cycle:** `23e5c754b801-2026082009-bd509872`
**Source commit:** `23e5c754b801b834894cb562649ba1ad4b716ff5`
**Evidence SHA-256:** `bd509872ef90dd09242f1c0041b96198975a7544cd7d782897aebb310c138e5b`
**Provider:** copilot
**Scope:** THREE_AGENT_STRATEGY_REVIEW
**Reviewed at:** 2026-08-20T10:19:27Z
**Status:** CHANGES_PROPOSED
**Review only / No live changes:** true / true

---

## Runtime Evidence Gaps

Fresh runtime evidence (within 2 hours) was **not available** for this cycle. The following evidence gaps exist:

- No live or canary trade logs are available; money-weighted net P&L, win rates, and profit factors after costs cannot be confirmed.
- `strategy_lab_windows` table contents are unknown — shadow evaluation counts, profit factors and eligible-opportunity participation metrics are unavailable.
- No EVM or Solana execution logs were provided for this cycle.

These gaps do not block the architecture review. Profitability and live-readiness claims are withheld pending fresh runtime evidence.

---

## Architecture Summary

The repository implements a dual-chain (Solana + EVM) trading bot with a **Strategy Laboratory** pattern. Key governance properties observed at the source commit:

- All new strategies start in **SHADOW** status and require `MIN_EVALUATION_WINDOWS=3`, `MIN_EVALUATION_TRADES=8`, `MIN_ELIGIBLE_OPPORTUNITIES=10`, and `MIN_ELIGIBLE_PARTICIPATION=0.20` before promotion consideration.
- A minimum `MIN_PROFIT_FACTOR=1.10` is enforced as a promotion gate.
- Execution safety gates (simulation/preflight, slippage, sellability, liquidity checks, circuit breakers) are structurally present.
- AI proposals are validated against a schema that blocks credential and deployment-instruction injection via `FORBIDDEN_SPEC_TERMS`.
- The `three_agent_strategy_contract.py` policy layer enforces `confidence >= 0.85`, two independent supporting agents, `shadow_only=true`, and a bounded file allow-list before any auto-code action is permitted.

---

## Proposals

### COPILOT-STRAT-001 — Profitable-wallet cohort momentum signal

**Action:** RESEARCH_MORE  
**Chains:** SOLANA, EVM  
**Confidence:** 0.45  
**Risk class:** LOW  

**Hypothesis:** Tokens that appear repeatedly in the recent buy history of a cohort of independently profitable wallets (each verifiably net-positive over ≥ 30 days, money-weighted) exhibit short-term positive price momentum sufficient to recover round-trip costs on at least 55% of eligible trades.

**Differentiation from leader copying:** Uses a multi-wallet cohort rather than a single leader. The signal is the *intersection* of independently profitable behaviour, not a copy of one wallet's positions. This is more robust to single-wallet failure and less exposed to front-running a single known address.

**Research plan:**
1. Use Dune to build a cohort of ≥ 20 wallets with verifiable 30-day net-positive money-weighted P&L on both Solana and EVM.
2. Identify tokens present in ≥ 3 cohort wallets' recent buys within a 4-hour window.
3. Shadow-simulate entry/exit on those tokens for 2 weeks.
4. Compare simulated gross P&L against estimated round-trip costs (DEX fees, gas, slippage from Jupiter/DEX Screener quotes).

**Research tools:** Dune, DEX Screener API, Jupiter, Etherscan API V2

**Shadow test (falsifiable):** Register with SHADOW status. After ≥ 8 trades across ≥ 3 evaluation windows per chain, measure money-weighted net P&L after fees/gas/slippage. **Falsified** if `profit_factor < 1.10` or `eligible-opportunity participation < 0.20`.

**Suggested files:** `learnerbot/cross_chain_strategy_signals.py`, `learnerbot/strategy_lab_research.py`, `tests/test_cross_chain_strategy_signals.py`

---

### COPILOT-STRAT-002 — New-liquidity quality filter for early-pool tokens

**Action:** RESEARCH_MORE  
**Chains:** SOLANA, EVM  
**Confidence:** 0.40  
**Risk class:** LOW  

**Hypothesis:** Tokens whose liquidity pools are ≤ 6 hours old, have ≥ $50k locked liquidity, pass sellability simulation, show ≥ 3× volume-to-liquidity ratio within the first hour, and whose deployer address has no prior rug-pull association exhibit a short-term positive net edge after costs in at least 50% of shadow trades.

**Differentiation:** Focuses on objective, measurable pool-quality metrics rather than any wallet's behaviour. Does not rely on leader copying.

**Research plan:**
1. Use DEX Screener API to identify new pools meeting age, liquidity and volume criteria on Solana and EVM.
2. Cross-reference deployer addresses against known rug-pull lists (Dune, public blocklists).
3. Run 2-week shadow simulation.
4. Measure slippage, gas/priority cost and actual exit prices versus simulated.

**Research tools:** DEX Screener API, Dune, Jupiter, Etherscan API V2

**Shadow test (falsifiable):** Register with SHADOW status. For each qualifying new pool, record shadow entry/exit and measure net P&L after fees. **Falsified** if `profit_factor < 1.10` after ≥ 10 eligible opportunities, or if ≥ 30% of trades result in exit failure or sellability gate block.

**Suggested files:** `learnerbot/strategy_lab_research.py`, `learnerbot/cross_chain_strategy_signals.py`, `tests/test_strategy_new_liquidity.py`

---

### COPILOT-STRAT-003 — Existing SHADOW strategies: extend evaluation window

**Action:** SHADOW_MORE  
**Chains:** SOLANA, EVM  
**Confidence:** 0.90  
**Risk class:** LOW  

**Hypothesis:** Any SHADOW strategy in the registry that has fewer than `MIN_EVALUATION_WINDOWS=3` or `MIN_EVALUATION_TRADES=8` should continue to accumulate shadow evidence before any promotion decision is made.

**Note:** This is a governance recommendation, not a new strategy. Without runtime evidence it cannot be confirmed whether any SHADOW strategy currently meets these thresholds.

**Shadow test (falsifiable):** Confirm via `strategy_lab_windows` query that each SHADOW strategy has completed ≥ 3 evaluation windows and ≥ 8 trades before any promotion candidate review. **Falsified** if a strategy is promoted before these thresholds are met.

---

## Safety Boundary Confirmation

This review:
- does **not** propose removing or bypassing simulation/preflight;
- does **not** weaken positive-edge, liquidity, sellability or slippage checks;
- does **not** widen risk to increase trade count;
- does **not** propose capital increases;
- does **not** bypass nonce/atomicity/private-routing/circuit-breaker controls;
- contains **no** API keys, wallet private keys, seed phrases or signing material;
- does **not** request auto-adding any asset to LIVE;
- does **not** request auto-merging or auto-deploying any strategy to LIVE.

All proposals start in SHADOW and require out-of-sample evidence before any further action.
