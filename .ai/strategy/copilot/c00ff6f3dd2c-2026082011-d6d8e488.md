# Copilot Hourly Strategy Review

| Field | Value |
|---|---|
| **Cycle** | `c00ff6f3dd2c-2026082011-d6d8e488` |
| **Source commit** | `c00ff6f3dd2c579403075af3b70825e8f3cbc917` |
| **Evidence SHA-256** | `d6d8e48800d1e02f44901f12b047a9ab4b2e7131743164b0a088dafeb8723938` |
| **Reviewed at** | 2026-08-20T11:20:30Z |
| **Status** | CHANGES_PROPOSED |
| **Runtime evidence fresh** | No (>2 h) |
| **Provider** | copilot |
| **Scope** | THREE_AGENT_STRATEGY_REVIEW |
| **Review only** | true |
| **No live changes** | true |

---

## Runtime Evidence Note

Runtime evidence is not fresh for this cycle. No profitability, canary, or live-readiness claims are made. The architecture review of source commit `c00ff6f3dd2c` is complete and forms the basis of the proposals below.

---

## Executive Summary

The Strategy Lab at source commit `c00ff6f3dd2c` enforces a sound SHADOW-first promotion model with profit-factor gating (≥ 1.10), minimum evaluation windows/trades (3 windows, 8 trades), and eligibility-participation checks (≥ 20% of ≥ 10 eligible opportunities). These architectural controls are correctly placed and should not be weakened.

The primary identified gap is **single-leader-wallet dependency** as the sole edge source. The current `copy_engine.py` and `LEADER_COPY` source type concentrate risk on one observable wallet's behaviour, which can degrade if that wallet changes strategy, is front-run, or is identified and blocked.

Three proposals are raised for SHADOW evaluation or further research:

1. **LCM-001 — Liquidity-Confirmed Momentum** (NEW_SHADOW, confidence 0.72): a chain-native signal derivable from DEX pool state without leader-wallet dependency.
2. **NPQ-002 — New-Pool Quality Scoring** (NEW_SHADOW, confidence 0.68): quality-gated new-pool entry exploiting the platform's existing preflight sellability/liquidity infrastructure.
3. **WCP-003 — Profitable-Wallet Cohort Pattern Extraction** (RESEARCH_MORE, confidence 0.61): expanding the wallet universe from one leader to a Dune-derived cohort before promoting to SHADOW.

No live changes, capital changes, risk widening, or signing-material access is proposed. All proposals start in SHADOW.

---

## Proposals

### LCM-001 — Liquidity-Confirmed Momentum

| | |
|---|---|
| **Action** | NEW_SHADOW |
| **Confidence** | 0.72 |
| **Chain scope** | SOLANA, EVM |

**Hypothesis:** Tokens with sustained DEX volume growth and deep liquidity relative to market-cap over a 15–60 min window show positive mean net P&L after swap costs, independently of any leader wallet.

**Rationale:** Leader-wallet copying introduces single-point-of-failure risk and execution lag. Liquidity-confirmed momentum is a signal derivable entirely from on-chain pool state (DEX Screener API / Jupiter quote), is independently falsifiable, and has academic precedent on EVM markets. Replicating it on Solana requires a separate chain-specific fee and price-impact model.

**Evidence paths:**
- `learnerbot/cross_chain_strategy_signals.py` — existing cross-chain signal adapter; no volume/liquidity momentum feature is currently implemented.
- `learnerbot/strategy_lab.py` — profit-factor and evaluation gates already in place for SHADOW promotion.
- `learnerbot/strategy_lab_research.py` — research helper; no liquidity momentum indicator present — gap to fill.

**Shadow test (falsifiable):** Run SHADOW-only for ≥ 72 h on both chains. Accept if `profit_factor ≥ 1.10` across ≥ 8 completed trades and ≥ 10 eligible opportunities with ≥ 20% participation rate. Reject if `profit_factor < 1.0` or largest single loss > 3× average gain after 72 h.

**Research plan:**
1. Pull 7-day DEX Screener pool history for top-50 Solana and top-50 EVM tokens by volume.
2. Compute rolling 15-min and 60-min volume-change and liquidity-depth ratio.
3. Backtest entry on momentum threshold, exit at 10 min or 2% adverse move.
4. Measure net P&L after simulated swap fees and price impact.

**Research tools:** DEX Screener API, Jupiter quote simulation, Dune (EVM cohort)

**Suggested files:**
- `learnerbot/cross_chain_strategy_signals.py`
- `learnerbot/strategy_lab_research.py`
- `tests/test_cross_chain_strategy_signals.py`

---

### NPQ-002 — New-Pool Quality Scoring

| | |
|---|---|
| **Action** | NEW_SHADOW |
| **Confidence** | 0.68 |
| **Chain scope** | SOLANA, EVM |

**Hypothesis:** New liquidity pools that satisfy a composite quality score (age < 6 h, locked liquidity, verified contract on EVM, holder count growth, volume/liquidity ratio) show positive net P&L over a 2–4 h hold when entered within the first 30 min of pool creation.

**Rationale:** New-pool sniping is a distinct alpha source from leader-copying. Quality gating (sellability check, liquidity lock, initial holder growth) is already partially supported by the platform's preflight checks. This proposal formalises a SHADOW scoring layer rather than bypassing any existing safety gate.

**Evidence paths:**
- `learnerbot/strategy_lab.py` — `FORBIDDEN_SPEC_TERMS` and sellability/liquidity checks show the platform already has the concept of pool quality gates.
- `learnerbot/strategy_ai_proposals.py` — AI proposal registry; new-pool quality strategy can be registered as `AI_PROPOSED` source.

**Shadow test (falsifiable):** Shadow-run for ≥ 48 h on each chain independently. Accept per chain if `profit_factor ≥ 1.15`, participation ≥ 25% of scored qualifying pools, and zero rug-pull execution losses. Reject if any rug-pull loss occurs or `profit_factor < 1.0` after 48 h.

**Research plan:**
1. Extract last 14 days of new pool creations from Dune (EVM) and DEX Screener (Solana).
2. Score each pool at T+0, T+15, T+30 min.
3. Simulate entry/exit at scored thresholds.
4. Report net P&L, largest loss, and participation rate.

**Research tools:** Dune, DEX Screener API, Etherscan API V2

**Suggested files:**
- `learnerbot/strategy_lab_research.py`
- `learnerbot/strategy_ai_proposals.py`
- `tests/test_strategy_new_pool_quality.py`

---

### WCP-003 — Profitable-Wallet Cohort Pattern Extraction

| | |
|---|---|
| **Action** | RESEARCH_MORE |
| **Confidence** | 0.61 |
| **Chain scope** | SOLANA, EVM |

**Hypothesis:** Aggregating repeated route fingerprints across a cohort of independently profitable wallets (not a single selected leader) yields route patterns with higher replication confidence and lower single-wallet dependency than the current `LEADER_COPY` source.

**Rationale:** The current `strategy.py` `learn_patterns` function already computes cohort-level confidence (`wallet_component`, `repeat_component`). Expanding the wallet universe from one selected leader to a Dune-derived cohort of wallets with net positive P&L over 30 days diversifies the edge source without bypassing any existing safety gate.

**Evidence paths:**
- `learnerbot/strategy.py` — `learn_patterns` computes `wallet_component` and `replicability`; designed to support multiple wallets but currently constrained by the input wallet set.
- `learnerbot/copy_engine.py` — current leader-copy engine; single-leader dependency is the gap.

**Shadow test (falsifiable):** Cannot be fully shadow-tested until cohort wallet data is collected via Dune. RESEARCH_MORE: run Dune query for EVM wallets with ≥ 20 on-chain swaps and net positive P&L over 30 days. If cohort size ≥ 10 wallets, re-evaluate as NEW_SHADOW with confidence target ≥ 0.75.

**Research plan:**
1. Build Dune query: EVM DEX trades, group by wallet, net P&L > 0, ≥ 20 swaps in 30 days, excluding known bots/sandwichers.
2. Extract route fingerprints for top-30 wallets.
3. Cross-reference with existing `strategy_patterns` table.
4. Re-score confidence using `wallet_component` from `strategy.py` logic.

**Research tools:** Dune, Etherscan API V2, GitHub public code search

---

## Architecture Observations

| Area | Observation |
|---|---|
| SHADOW gate | Correctly enforces `profit_factor ≥ 1.10`, `MIN_EVALUATION_TRADES = 8`, `MIN_EVALUATION_WINDOWS = 3` before any promotion candidate. No changes needed. |
| Edge diversity | Current strategies are concentrated on `LEADER_COPY` source. Diversification proposals above address this. |
| Cross-chain model | `cross_chain_strategy_signals.py` exists but no chain-specific fee/impact differentiation is evident for Solana vs EVM. Proposals LCM-001 and NPQ-002 require separate per-chain shadow tests. |
| Forbidden spec terms | `FORBIDDEN_SPEC_TERMS` in `strategy_lab.py` correctly blocks credentials and deployment material from entering the strategy registry. No changes needed. |
| Runtime evidence | Stale (>2 h). Next cycle should include fresh `profit_evidence`, `strategy_patterns` counts, and participation-rate statistics before any confidence upgrade. |

---

## Safety Boundary Confirmation

- No live trades, capital changes, or risk widening are proposed.
- No signing material, private keys, seed phrases, or API secrets are referenced.
- No existing execution safety gates (simulation/preflight, slippage, sellability, circuit-breakers) are relaxed.
- All proposals start in SHADOW and require explicit human approval before any live promotion.
- `live_auto_deploy = false`, `draft_pr_only = true` for any downstream master decision.
