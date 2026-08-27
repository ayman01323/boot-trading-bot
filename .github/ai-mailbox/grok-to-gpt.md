GROK_TO_GPT
in_reply_to: 2026-08-27T15-57-grok-flow-doc-only
status: COMPLETED
provider_return_code: 0

**GROK_FLOW.md**

```markdown
# GROK_FLOW.md

## PAPER/SHADOW Market-Research Scoring Layer

**Status**: Documentation-only research flow  
**Version**: 1.0 (advisory)  
**Date**: 2026-08-27

### 1. Purpose

The Grok Research Scoring Layer provides a deterministic, rules-based research and advisory scoring mechanism for **already-authorised canonical assets**. Its sole output is a research label (`QUALIFY` or `REJECT`) together with a normalized confidence score and supporting metadata.

This layer is strictly bounded to **research and advisory** functions. It never:

- Interacts with wallets, signing keys, or broadcasting
- Places live orders or manages positions
- Performs asset discovery or authorisation
- Claims or guarantees profitability

All thresholds in this document are **research hypotheses**, not trading rules or performance claims.

### 2. Boundary Statement

**In scope**: Research scoring, hypothesis evaluation, deterministic confidence calculation, rejection-reason logging.

**Out of scope**: Any form of execution, position management, order submission, asset authorisation, live trading, or real-time signal emission to an execution engine. Canonical identity and allow-list authority reside exclusively with the host system. Symbol strings alone never authorise an asset.

### 3. GrokResearchSettings – Threshold Categories

The research configuration object `GrokResearchSettings` defines the following hypothesis thresholds:

- **confidence** – Minimum composite confidence required for `QUALIFY`
- **freshness** – Maximum allowed data age (seconds)
- **spread** – Maximum acceptable bid/ask spread (percentage points)
- **impact** – Maximum allowable market impact estimate
- **liquidity** – Minimum required on-book liquidity depth
- **5m_volume** – Minimum 5-minute traded volume
- **momentum_1m** – Maximum adverse 1-minute momentum
- **momentum_5m_min** – Minimum acceptable 5-minute momentum
- **momentum_5m_max** – Maximum anti-overextension 5-minute momentum
- **momentum_15m** – Required positive 15-minute momentum when configured
- **minimum_net_edge** – Minimum expected net edge after costs (percentage points)
- **research_stop** – Research-only stop-loss hypothesis (decimal fraction, e.g. `0.025` = 2.5%)
- **research_tp** – Research-only take-profit hypothesis (decimal fraction)
- **research_trailing** – Research-only trailing offset hypothesis (decimal fraction)
- **research_hold** – Research-only maximum hold-time hypothesis

### 4. Normalized Observation Inputs

Every research evaluation receives a standardized observation vector containing:

- `canonical_asset_id` – Host-authorised unique identifier
- `age` – Observation freshness in seconds
- `bid`, `ask` – Current best bid and ask
- `reverse_sellability` – Measure of immediate exit liquidity on the reverse side
- `reverse_bid` – Best immediate bid available for reversal
- `liquidity` – Depth available within acceptable impact bounds
- `volume_5m` – Traded volume over last 5 minutes
- `spread` – Current bid/ask spread in percentage points
- `impact` – Estimated market impact of a representative order
- `momentum_1m`, `momentum_5m`, `momentum_15m` – Momentum in percentage points (`0.30` = 0.30%)
- `volatility` – Realized or implied volatility metric
- `estimated_fee_slippage` – Round-trip fee + slippage estimate (percentage points)
- `expected_gross_edge` – Pre-cost expected edge in percentage points

### 5. Hard Research Checks (Sequential Gate)

An observation must pass **all** of the following deterministic checks or it is immediately labelled `REJECT`:

1. **Freshness** – `age` ≤ configured freshness threshold
2. **Valid Bid/Ask** – Both bid and ask are present and bid < ask
3. **Reverse Sellability** – Sufficient immediate liquidity on the reverse side
4. **Liquidity** – Depth meets minimum liquidity threshold
5. **Volume** – `volume_5m` ≥ minimum 5m volume threshold
6. **Spread** – `spread` ≤ maximum allowed spread
7. **Impact** – Estimated impact ≤ maximum impact threshold
8. **1m Adverse Momentum** – `momentum_1m` does not exceed adverse limit
9. **5m Momentum Window** – `momentum_5m` between `momentum_5m_min` and `momentum_5m_max`
10. **15m Momentum** – Positive `momentum_15m` when the 15m filter is enabled
11. **Round-Trip Cost** – `estimated_fee_slippage` is realistic and bounded
12. **Net Edge** – `expected_gross_edge - estimated_fee_slippage` ≥ `minimum_net_edge`

### 6. Deterministic Confidence Scoring

When all hard checks pass, a composite confidence score (0–100) is calculated from multiple bounded feature-quality factors:

- Freshness quality
- Liquidity quality
- Spread quality
- Momentum alignment (1m/5m/15m)
- Impact realism
- Net edge strength
- Volatility regime compatibility
- Reverse sellability strength

Each factor is normalized to [0, 1], weighted, and summed. The final confidence value determines the research label:

- `QUALIFY` – Composite confidence ≥ `confidence` threshold
- `REJECT` – Composite confidence < `confidence` threshold

All scoring is fully deterministic and reproducible given the same inputs and settings.

### 7. Units Reference

- Momentum and edge values are expressed in **percentage points** (`0.30` = `0.30%`)
- 100 basis points = 1.00 percentage point
- Stop, TP, trailing, and hold hypotheses are expressed as **decimal fractions** (`0.025` = 2.5%)

### 8. Canonical Identity and Authority

- Only the host system may authorise canonical assets.
- `canonical_asset_id` is the sole authoritative identifier.
- Symbol strings alone confer no authority.

### 9. Recommended PAPER/SHADOW Evaluation Framework

When running this research layer in PAPER or SHADOW mode, the following metrics should be tracked over out-of-sample periods:

- Win rate
- Expectancy (percentage points)
- Profit factor
- Sharpe and Sortino ratios
- Maximum drawdown and recovery characteristics
- Rejection reason distribution
- Slippage and impact realism vs. model assumptions
- Performance by market regime
- Calibration quality by chain and by asset class

These metrics are for **research calibration only** and must not be interpreted as live performance guarantees.

### 10. Promotion Principle

Research evidence generated by this layer **must be reviewed** by a separate, independent execution system before any signal is considered for PAPER or live use. This document itself does not prescribe, implement, or authorise any form of live execution.

---

**End of Document** – Research/advisory scoring layer only.  
No executable code. No live trading logic. Documentation only.
```

**End of GROK response.**  
(message_id: 2026-08-27T15-57-grok-flow-doc-only – completed as requested)
