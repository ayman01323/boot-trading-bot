# SiBot 1 engine template

Copy this package structure into `sibot1_engines/<engine_id>/` and implement your independent strategy against `sibot1_engines._shared.contracts`.

Rules:

1. Consume normalized market data; do not build a private signer path.
2. Emit `TradeIntent` / `ExitIntent` only.
3. Put all strategy-specific limits/settings in your own CSV.
4. Central PoolCheck is mandatory after entry intent and may continue monitoring open positions.
5. Shared Capital/Position Manager owns the one-wallet virtual sub-account and lot attribution.
6. Your engine may request shared/dedicated/hybrid RPC through configuration, but secrets must remain secret references.
7. Add tests and a flow document with the PR.
