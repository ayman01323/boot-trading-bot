# Grok SiBot 1 — CompactFlow v1

Source: Grok's SiBot 1 flow and code attempts. GPT performed contract integration because Grok's generated packages repeatedly used fields/types outside the fixed shared v1 API.

Flow: `Shared Solana event -> volume velocity + Grok confidence + developer-selling filter -> TradeIntent(BUY) -> central capital reservation -> mandatory PoolCheck -> shared execution -> attributed Grok lot -> P&L/trend update -> Grok take-profit/stop/reversal ExitIntent`.

Grok cannot call PoolCheck, sign, broadcast, access keys, or exit another engine's lot. PoolCheck may independently trigger a central emergency exit while preserving Grok lot attribution.
