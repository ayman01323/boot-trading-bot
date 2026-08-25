# SiBot 1 CSV namespace

This directory is reserved for SiBot 1 configuration.

Planned common files:

- `engine_registry.csv` — engine identity/state/version metadata.
- `rpc_registry.csv` — shared/dedicated/hybrid RPC declarations using secret references, never raw secrets.
- `wallet_registry.csv` — logical wallet assignments/labels only, never private keys.
- `poolcheck/poolcheck.csv` — central mandatory PoolCheck/rug-protection configuration.
- `engines/<engine_id>/settings.csv` — live settings controlled by each engine's strategy governance.

Agent PRs should commit an engine-local `settings.example.csv`, not real secrets or production wallet keys.
