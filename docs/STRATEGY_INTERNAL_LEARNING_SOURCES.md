# Strategy Lab First-Party Learning Sources

Strategy Lab treats the bot's own measured evidence as the highest-priority research input before external research.

## INT1 — Learning Bot Internal Evidence

INT1 is built from the bot's own chain databases and existing Strategy Lab research functions. It includes:

- proved `profit_evidence` with realised net-base outcomes;
- learned `strategy_patterns` with transaction count, wallet count, proved-profit count, confidence and replicability;
- profitable-wallet cohorts with wallet identities anonymised in the report;
- cross-chain pattern portability analysis.

The objective is to learn repeated economic behaviour that survived real observed costs and appears replicable. A
historical profitable result is evidence, not automatic permission to trade. Findings become falsifiable SHADOW hypotheses.

## INT2 — SiBot Observed-Wallet Learning

INT2 explicitly exposes SiBot's learning-from-others evidence to Strategy Lab in anonymised form. It reads existing
first-party tables where available:

- `behaviour_rankings`;
- `wallet_behaviour_rankings`;
- `copy_wallet_candidates`;
- `copy_trade_recommendations`.

The wider SiBot/Learning Bot database also records `wallet_scores`, `profit_evidence`, `trade_behaviour_evidence` and
`strategy_patterns`; those feed the rankings/pattern evidence used by the Lab.

Strategy Lab must learn the **behaviour**, not blindly copy a wallet. It should prefer patterns that are repeated across
multiple profitable wallets, have proved samples, positive realised net after costs, acceptable downside and evidence of
replicability. Rejected candidates and rejection reasons are useful negative evidence and should not be discarded.

Wallet addresses are hashed in the Strategy Lab research payload. INT2 cannot authorise LIVE trading.

## Mandatory research order

1. **INT1 — Learning Bot**: proved outcomes and learned patterns.
2. **INT2 — SiBot**: observed-wallet behaviours, rankings, candidate evidence and recent recommendations.
3. **EXT1–EXT4**: fresh external DefiLlama/GitHub/arXiv evidence.
4. **Curated external catalogue**: Dune, DEX Screener, Etherscan, Jupiter, market-data, backtesting, execution and academic references.

External evidence is used to corroborate, challenge or extend first-party learning. It should not override stronger measured
internal evidence without a clear reason.

## Safety rule

A wallet is evidence, not a strategy. A research source can create a SHADOW hypothesis, but it cannot create permission to
trade. CANARY/LIVE remains subject to the existing three-agent review, GPT Master reconciliation, exact-source review,
executable-cost, simulation, liquidity, sellability, risk and promotion gates.
