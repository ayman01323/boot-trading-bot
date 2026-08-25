# Gemini SiBot 1 — Gemini-PulseFlow v1

Source: Gemini's SiBot 1 code/design responses. GPT performed the contract-integration pass because Gemini's bus reply was truncated and initially used fields outside the shared v1 contract.

Flow: `Shared Solana MarketEvent -> freshness/liquidity/volume pulse gate -> TradeIntent(BUY) -> central capital reservation -> mandatory PoolCheck -> shared quote/simulation/executor -> attributed Gemini lot -> Gemini position update -> take-profit ExitIntent -> shared executor -> realised P&L`.

The engine does not call PoolCheck, sign, broadcast, read keys or mutate the shared wallet. Strategy-specific values stay in Gemini's CSV. Optional normalized `volume_velocity` / `liquidity_velocity` payload values may raise signal confidence; missing mandatory market data returns no trade.
