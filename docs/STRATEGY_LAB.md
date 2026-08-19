# Competitive Strategy Laboratory

## Objective

The learning bot must not depend only on following leader wallets. It should maintain a portfolio of independently measured strategy families, discover and test new ideas, improve strategies that have recoverable weaknesses, and replace strategies that remain economically poor after a meaningful sample.

The objective is **money-weighted net profitability after recorded costs**, not raw trade count or win rate.

## Core rule: active does not mean forced trading

An ACTIVE strategy is expected to scan continuously and participate in a reasonable share of opportunities that satisfy:

1. the strategy's own signal/edge rule; and
2. the platform's common executability and safety requirements.

It is not required to manufacture a trade when no positive edge exists. Forcing every strategy to trade every hour would turn an activity target into a loss-generating quota.

The laboratory therefore records three separate quantities:

- `opportunities`: market situations considered;
- `eligible_opportunities`: situations that passed the strategy's signal and common prerequisites;
- `trades`: simulated or executed participation.

A strategy with zero eligible opportunities is not automatically bad. A strategy that repeatedly identifies many eligible opportunities but takes almost none is flagged `REWORK` as potentially over-restrictive.

## Strategy sources

The laboratory supports:

- `LEADER_COPY` — existing selected-leader copying;
- `LEARNED_PATTERN` — patterns learned from profitable public routes/behaviour without depending on a single wallet;
- `MARKET_NATIVE` — strategies derived directly from market conditions;
- `AI_PROPOSED` — new falsifiable strategies suggested by GPT, Gemini, Copilot or another authorised research agent;
- `OPERATOR` — an operator-defined strategy hypothesis.

Every new strategy, including operator strategies, starts `SHADOW` in the laboratory. Registration itself cannot arm LIVE trading.

## Initial non-leader research families

The first built-in hypotheses are deliberately asset-neutral:

1. **Cross Venue Net Arbitrage** — executable cross-venue discrepancies after costs and latency reserve.
2. **Liquidity Confirmed Momentum** — momentum confirmed by liquidity and transaction-flow expansion.
3. **Dislocation Mean Reversion** — reversion after temporary dislocations where liquidity has not structurally deteriorated.
4. **Flow Acceleration** — independent transaction-flow acceleration that does not depend on one leader wallet.
5. **New Liquidity Quality** — new-market/pool ranking using liquidity, sellability, dispersion and execution quality before considering an entry.
6. **Learned Route Replication** — repeated profitable route structures learned from the existing `strategy_patterns` evidence.

These are research hypotheses, not claims that they are profitable.

## Lifecycle

`PROPOSED -> SHADOW -> PROBATION -> PROMOTION_CANDIDATE`

or, when evidence is weak/bad:

`SHADOW/ACTIVE -> REWORK -> REPLACE -> RETIRED`

The laboratory itself never turns `PROMOTION_CANDIDATE` into LIVE.

## Separate scorecard for every strategy

Every observation window records:

- opportunities and eligible opportunities;
- trades;
- wins and losses;
- gross profit and gross loss;
- fees;
- measured slippage cost;
- net profit;
- largest loss;
- execution failures;
- signal skips;
- strategy/version metadata.

The evaluator then reports net P&L, profit factor and opportunity participation separately for every strategy.

## Replacement logic

A strategy is not replaced merely because it has a quiet hour.

After a sufficient sample:

- negative net result or profit factor <= 1 -> `REPLACE_OR_REWORK`;
- substantial eligible opportunity count but very low participation -> `REWORK_FILTERS`;
- positive net result with research profit factor >= 1.10 -> `PROMOTION_CANDIDATE`, still requiring independent validation;
- too little evidence -> keep testing.

A high win count does not rescue a money-losing strategy. Nine small wins and three large losses can correctly result in replacement.

## Creative AI strategy proposals

AI reviewers should be allowed to propose new strategy hypotheses, but the proposal must be declarative data rather than arbitrary executable code. Each proposal should specify:

- name and family;
- hypothesis and why an edge may exist;
- market regime in which it should work;
- entry signal;
- exit logic;
- data required;
- expected execution costs;
- failure modes;
- shadow test;
- minimum evaluation sample;
- conditions that falsify/reject it;
- relationship/correlation with existing strategies.

The AI should actively search for strategies that are **different** from leader copying rather than merely changing leader thresholds.

## Hourly AI review

The hourly evidence package should expose the Strategy Laboratory portfolio alongside actual trading forensics. GPT/Gemini/Copilot should each review:

- which strategies generated opportunities;
- which participated;
- which made/lost money after costs;
- which losses came from strategy selection versus execution;
- which strategies are becoming too restrictive;
- which should be improved, replaced or retired;
- whether a new uncorrelated strategy hypothesis should be added.

GPT synthesis should not use simple voting. Objective evidence outranks model agreement.

## 15-minute monitor

The 15-minute loop is a safety/health loop, not a strategy-optimisation loop. It may pause new entries or quarantine a strategy when there is an execution fault or abnormal loss condition, but it must not loosen safety controls or increase risk merely to force trading.

## Next execution boundary

This branch implements the strategy registry, independent scorecards, creative hypothesis seeding, lifecycle evaluation, replacement/rework logic and hourly-report integration hook.

Actual signal/execution adapters for a new family should be implemented one family at a time and must reuse the existing common execution protections. A new strategy must not create a second, weaker transaction execution path.
