# Multi-Chain Learning Bot v2.2 — Fast Graph-First Direct Market + Multi-User

v2.2 keeps the v2.1 five-chain learning/copy engine and adds an **independent fast direct-market loop**. The fast loop no longer waits for the multi-minute wallet-learning cycle and no longer guesses token-to-token edges that do not exist.

## What changed in v2.2

- **Independent fast market thread**: current-market discovery starts shortly after systemd startup and targets a 15-second cadence (`fast_market_interval_seconds`). If a pass takes longer, passes do not overlap.
- **Graph-first triangle construction**: candidates are created only from actual V2 factory pairs in the local pool graph. This removes the v2.1 pattern where nearly every A→B combination failed with `missing_v2_pair`.
- **Liquid seed bootstrap**: `tokens.csv` contains operator-editable liquid seeds. `factory.getPair()` is used to discover seed pairs immediately instead of waiting for a full factory crawl.
- **Multi-venue V2 architecture**: every enabled V2 row in `dex_registry.csv` can be scanned. A route carries its router address, and wallet simulation/execution accepts a router override only when that router is an enabled V2 registry entry.
- **Separate learned snapshot**: learned-wallet candidates are stored in `CSVbot/auto/learned_route_opportunities.csv`; the fast direct scanner merges them with current direct candidates into `CSVbot/live_opportunities.csv`.
- **Fast execution gate**: when AUTO is enabled, the fast worker re-runs each user's wallet-specific `simulate_cycle()` before any signing attempt. MASTER LIVE/AUTO remain OFF after upgrade.

## Enabled chains

All five EVM chains remain enabled:

- BSC (56)
- Base (8453)
- Ethereum (1)
- Arbitrum One (42161)
- Polygon PoS (137)

Default V2 venues are PancakeSwap V2 on BSC/Base/Ethereum/Arbitrum and QuickSwap V2 on Polygon. Additional compatible V2 venues can be added in `CSVbot/dex_registry.csv`.

Solana is not represented as an EVM chain and is not executed by this build.

## Important files

- `CSVbot/auto/pool_registry.csv` — discovered V2 pair graph.
- `CSVbot/auto/direct_market_state.csv` — factory crawl cursors.
- `CSVbot/auto/direct_market_opportunities.csv` — current fast direct candidates.
- `CSVbot/auto/direct_market_rejections.csv` — graph/quote/liquidity/edge rejection diagnostics.
- `CSVbot/auto/learned_route_opportunities.csv` — learned-wallet candidates.
- `CSVbot/auto/fast_market_status.csv` — last fast-pass status and duration.
- `CSVbot/live_opportunities.csv` — merged execution feed read by Telegram/AUTO.

## Upgrade from v2.1

Extract as:

```bash
/root/multichain-learning-bot-v2.2-fast-direct-market
```

Then:

```bash
cd /root/multichain-learning-bot-v2.2-fast-direct-market
chmod +x upgrade_from_v21.sh self_test.sh
./upgrade_from_v21.sh
```

The upgrade copies `.env`, SQLite databases, encrypted user-wallet files and current CSV state from v2.1 (or v2.0 as a fallback), adds v2.2 settings/seeds, runs compile/tests, installs the v2.2 systemd unit, and resets **MASTER LIVE = OFF** and **MASTER AUTO = OFF**.

## Verify the fast scanner

```bash
journalctl -u learnerbot -f
```

Within the first passes you should see lines like:

```text
[fast-market-scan] direct=0 merged=0 eligible=0 auto-events=0 seconds=...
```

The long learning cycle continues independently and logs:

```text
[scan:bsc] ...
[scan:base] ...
[scan:ethereum] ...
[scan:arbitrum] ...
[scan:polygon] ...
[live-route-scan] learned=... fast-direct=... routes=... eligible-for-wallet-sim=...
```

Telegram `/opportunities` shows `[DIRECT]` or `[LEARNED]` and performs a wallet-specific simulation against the route's registered router.

## Fast-market settings

`CSVbot/auto_trading_settings.csv` includes:

- `fast_market_enabled=true`
- `fast_market_interval_seconds=15`
- `fast_market_max_candidate_checks=120`
- `fast_market_max_routes_per_pass=20`
- `fast_market_pairs_per_dex_pass=6`
- `direct_market_max_seed_tokens_per_chain=8`
- `direct_market_seed_pair_checks_per_venue=28`

Do not lower profitability or safety gates merely to force a transaction. Zero candidates is a valid market result.

## Execution scope

v2.2 can scan multiple **V2 venues**, but a single route is still executed through one registered V2 router. It deliberately does **not** perform non-atomic two-transaction cross-DEX arbitrage. Atomic multi-router cross-DEX execution needs a separately deployed/audited executor contract and is not claimed by this release.

## Self-test

```bash
./self_test.sh
```

The self-test compiles the package, verifies all five enabled chains/RPC rows, checks v2.2 fast settings and seed coverage, and runs the included pytest suite when pytest is installed.

## Security

Private keys are not stored in CSV. Upgrade copies the existing encrypted wallet store locally. Keep the ZIP and server configuration private. MASTER LIVE and MASTER AUTO are deliberately reset OFF during upgrade.
