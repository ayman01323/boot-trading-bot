# GPT SiBot 1 Engine — NetEdgeArb v1

**Chain:** Base. **Venues:** cross-DEX, initially Aerodrome / Uniswap V3 compatible spread events.

Flow:

`Shared Data Hub dex_spread event -> validate freshness -> gross edge - estimated costs -> minimum net edge -> TradeIntent(ARBITRAGE) -> shared capital reservation -> mandatory PoolCheck -> fresh quote/simulation -> shared executor -> receipt/P&L attribution`.

The engine does not sign or broadcast. It requests an atomic arbitrage route and marks `atomic_required=true`. If shared execution unexpectedly leaves an attributed open lot, the engine asks to close only its own lot after `max_open_ms` or its configured emergency-loss threshold.

Learning telemetry should compare predicted net edge with realised net P&L, quote age, gas, fees, slippage and latency. GPT may later request changes to its own settings/code through the normal GPT Controller review path.
