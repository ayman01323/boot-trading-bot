# GROK_FLOW.md

## PAPER/SHADOW Market-Research Scoring Layer

**Status**: Research/advisory flow  
**Version**: 1.0  
**Date**: 2026-08-27

> Authorship note: Grok supplied the original research-flow document. GPT integration corrected unit/interface wording so this document matches the implemented settings and scorer: confidence is `[0,1]`, spread/impact inputs are basis points, valid prices allow `ask >= bid`, and net cost uses the explicit fee/slippage/impact fields.

### 1. Purpose

The Grok Research Scoring Layer provides a deterministic, rules-based research and advisory scoring mechanism for **already-authorised canonical assets**. Its output is a research label (`QUALIFY` or `REJECT`), a normalized confidence score, net-edge/cost estimates, feature values, and rejection reasons.

This layer is strictly bounded to research and advisory functions. It never:

- Interacts with wallets, signing keys, or broadcasting
- Places live orders or manages positions
- Performs asset discovery or authorisation
- Claims or guarantees profitability

All thresholds in this document are **research hypotheses**, not performance claims.

### 2. Boundary Statement

**In scope**: Research scoring, hypothesis evaluation, deterministic confidence calculation, and rejection-reason logging.

**Out of scope**: Wallet access, signing, broadcasting, order submission, live execution, position management, discovery, deployment, or asset authorisation. Canonical identity and allow-list authority reside exclusively with the host system. Symbol strings alone never authorise an asset.

### 3. `GrokResearchSettings` Threshold Categories

The research configuration defines:

- **Confidence** — minimum composite confidence required for `QUALIFY`
- **Freshness** — maximum allowed source-data age in seconds
- **Spread** — maximum acceptable bid/ask spread in basis points
- **Impact** — maximum allowable market impact in basis points
- **Liquidity** — minimum required liquidity in USD
- **5m volume** — minimum five-minute volume in USD
- **1m momentum** — minimum allowed one-minute momentum in percentage points
- **5m momentum minimum** — minimum acceptable five-minute momentum
- **5m momentum maximum** — anti-overextension ceiling
- **15m momentum** — strictly positive when configured
- **Minimum net edge** — minimum expected edge after modeled costs, in percentage points
- **Research stop** — stop-loss hypothesis as a decimal fraction
- **Research TP1/TP2** — take-profit hypotheses as decimal fractions
- **Research trailing** — trailing-drawdown hypothesis as a decimal fraction
- **Research hold** — maximum hold-time hypothesis in minutes

### 4. Normalized Observation Inputs

Every research evaluation receives:

- `canonical_asset_id` — host-authorised canonical identifier
- `source_age_seconds` — source age in seconds
- `bid`, `ask` — normalized bid and ask
- `reverse_sellable` — whether a reverse sell path exists
- `reverse_bid` — executable/research reverse bid
- `liquidity_usd` — liquidity in USD
- `volume_5m_usd` — five-minute volume in USD
- `spread_bps` — spread in basis points
- `impact_bps` — estimated impact in basis points
- `momentum_1m_pct`, `momentum_5m_pct`, `momentum_15m_pct` — momentum in percentage points
- `volatility_5m_pct` — five-minute volatility metric
- `estimated_fee_bps` — modeled fee component in basis points
- `estimated_slippage_bps` — modeled slippage component in basis points
- `expected_gross_edge_pct` — pre-cost research edge in percentage points

### 5. Hard Research Checks

An observation must pass all configured research checks:

1. **Freshness** — `source_age_seconds <= max_source_age_seconds`
2. **Valid bid/ask** — `bid > 0`, `ask > 0`, and `ask >= bid`
3. **Reverse sellability** — `reverse_sellable` is true and `reverse_bid > 0`
4. **Liquidity** — `liquidity_usd >= min_liquidity_usd`
5. **Volume** — `volume_5m_usd >= min_volume_5m_usd`
6. **Spread** — `spread_bps <= max_spread_bps`
7. **Impact** — `impact_bps <= max_impact_bps`
8. **1m adverse momentum** — `momentum_1m_pct >= momentum_1m_min_pct`
9. **5m momentum window** — configured minimum through anti-overextension maximum
10. **15m momentum** — strictly positive when enabled
11. **Cost estimate** — `(estimated_fee_bps + estimated_slippage_bps + impact_bps) / 100`
12. **Net edge** — `expected_gross_edge_pct - estimated_cost_pct >= min_net_edge_pct`

Any hard-gate failure produces `REJECT` plus explicit reasons.

### 6. Deterministic Confidence Scoring

After the hard research checks, the scorer combines bounded feature-quality factors including:

- Freshness quality
- Liquidity quality
- Five-minute volume quality
- Spread quality
- Impact quality
- One-minute momentum quality
- Five-minute momentum quality
- Fifteen-minute trend quality
- Net-edge quality

Each feature is normalized to `[0,1]`. The implemented feature weights sum to exactly `1.00`, and the final confidence is clamped to `[0,1]`.

- `QUALIFY` — all hard research gates pass **and** confidence is at least `min_confidence`
- `REJECT` — any hard gate fails or confidence is below `min_confidence`

A market observation can therefore pass all hard thresholds yet remain `REJECT` if its composite research quality is weak.

### 7. Units Reference

- Momentum and edge are **percentage points**: `0.30` means `0.30%`
- Spread, impact, fees, and slippage are **basis points**: `100 bps = 1.00 percentage point`
- Stop, TP, and trailing hypotheses are **decimal fractions**: `0.025 = 2.5%`
- Confidence is a normalized fraction in `[0,1]`

### 8. Canonical Identity and Authority

- Only the host may authorise canonical assets.
- `canonical_asset_id` is passed into the scorer by the host.
- A token symbol alone is never sufficient to authorise a non-native asset.
- The research layer cannot add, discover, or enable an asset.

### 9. Recommended PAPER/SHADOW Evaluation Framework

Track out-of-sample evidence including:

- Win rate
- Expectancy
- Profit factor
- Sharpe and Sortino ratios
- Maximum drawdown and recovery
- Rejection-reason distribution
- Slippage and impact realism versus modeled assumptions
- Performance by market regime
- Calibration by chain and canonical asset
- Confidence calibration versus realised outcomes

These metrics are research evidence only and are not live-performance guarantees.

### 10. Promotion Principle

The research label is advisory. The host remains responsible for canonical allow-list enforcement, risk limits, position state, PAPER accounting, and any separate future execution governance. This module contains no signer, wallet, transaction broadcast, exchange-order, or live-execution capability.

Research evidence should be reviewed out of sample before any separate system considers changing a deployment stage.
