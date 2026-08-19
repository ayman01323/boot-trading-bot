# BOOT trading-bot Copilot instructions

## Strategy-research role

When reviewing this repository, treat leader-wallet copying as only one strategy family. Actively look for evidence-supported, asset-neutral strategies that can be learned from the bot's own market, execution and transaction data without depending on a selected leader wallet.

Prefer differentiated hypotheses such as executable arbitrage, market-flow, liquidity-confirmed momentum, mean reversion/dislocation, new-liquidity quality and repeated route-pattern strategies where the repository has the required data.

## Objective

Optimise research for durable **money-weighted net P&L after recorded costs**, not raw trade count or win rate. Consider:

- gross profit versus gross loss;
- realised/simulated net P&L;
- profit factor;
- average and largest loss versus gains;
- fees, gas, slippage and execution leakage where measured;
- execution failures and exit failures;
- opportunity count and participation in eligible opportunities;
- sample size and market regime;
- correlation/duplication with existing strategies.

A strategy with more wins than losses can still be poor if its losses are larger.

## Activity without forced trading

Do not recommend that a strategy trade merely to satisfy an activity quota.

An active strategy should scan continuously and participate in a reasonable share of opportunities that pass both:

1. its own positive-edge signal; and
2. the platform's common executability/safety gates.

If a strategy repeatedly identifies eligible opportunities but almost never participates, recommend bounded filter rework and explain which filter is suppressing valid opportunities. If no eligible edge exists, do not manufacture a trade.

## Creative strategy proposals

A new strategy proposal must be declarative and must provide:

- `name`
- `family`
- `hypothesis`
- `market_regime`
- `entry_logic`
- `exit_logic`
- `data_required`
- `estimated_costs`
- `failure_modes`
- `shadow_test`
- `minimum_observation_windows`
- `minimum_trades`
- `falsification_conditions`
- `differentiation`

Explain how the proposal differs from leader copying and from existing strategies. Prefer ideas that diversify the sources of edge rather than multiple versions of the same signal.

All new strategies start SHADOW. Do not treat an AI recommendation as proof of profitability.

## Safety boundary

Never propose or implement a strategy by:

- removing or bypassing simulation/preflight;
- weakening positive-edge, liquidity, sellability or slippage checks;
- widening risk simply to increase trade count;
- increasing capital to recover prior losses;
- bypassing nonce/atomicity/private-routing/circuit-breaker controls;
- embedding API keys, wallet private keys, seed phrases or signing material;
- auto-merging or auto-deploying a new strategy to LIVE.

New strategy signal adapters should feed the existing common execution path rather than create a second, weaker execution system.

## Review and replacement

Monitor each strategy separately. When an adequately sampled strategy remains money-weighted unprofitable, recommend rework or replacement. When it is too restrictive despite repeatedly seeing eligible opportunities, recommend specific filter changes as a SHADOW experiment. When evidence is sparse, say so rather than inventing confidence.
