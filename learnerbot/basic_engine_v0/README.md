# Basic Trading Engine v0

This package is an **isolated reference engine**. It is not imported by the production CLI and is not deployed.

## Purpose

Rebuild the trading engine from a small, testable core while keeping clear extension points for the newer system requirements.

The core invariant is deliberately simple:

1. scan candidates;
2. obtain a fresh quote;
3. run every installed risk gate;
4. simulate the exact candidate;
5. require minimum expected profit;
6. obtain a second fresh quote immediately before execution;
7. repeat risk checks and simulation;
8. send the candidate through one serial execution owner.

Execution is disabled by default.

## First strategy: atomic arbitrage

The first strategy is deliberately **not buy-and-hold**. It proposes atomic round trips. The first real EVM adapter is intentionally limited to wrapped-native round trips such as:

`WETH -> TOKEN_A -> TOKEN_B -> WETH`

Using wrapped native as both the starting and ending asset lets v0 account for gas and profit in the same unit without adding a price oracle yet.

The strategy is accepted only when:

- the path returns to the exact starting asset;
- the quote is executable;
- the quoted input exactly matches the configured trade input;
- quoted price impact is below the configured maximum;
- quoted output covers input plus estimated gas;
- the remaining expected profit also covers a separate safety buffer;
- net profit after that safety buffer is at least the configured minimum;
- the engine then obtains a second fresh quote, repeats all risk gates, and simulates again immediately before any future execution.

The strategy plugin never receives a private key and cannot sign or broadcast. Pool-rug, honeypot, quarantine, exposure and chain-health controls remain separate fail-closed plugins so they can be added later without rewriting the arbitrage strategy.

## CSV-driven EVM V2 dry-run adapter

The first chain adapter is read-only and uses the existing CSV configuration model.

It reads:

- `chains.csv` for chain identity and wrapped-native address;
- `rpc_endpoints.csv` for enabled RPCs and priority;
- `dex_registry.csv` for the selected enabled V2 router;
- `basic_engine_v0_settings.csv` for v0 thresholds and optional public simulation address;
- `basic_engine_v0_routes.csv` for enabled atomic routes.

The new settings CSV uses the same scoped format as the existing bot:

`chain_id,setting,value,description`

Global settings use `*`; chain-specific rows override them.

Route CSV format:

`chain_id,route_id,path,input_amount_native,priority,enabled,description`

The `path` field uses `>` between token contract addresses.

Reference templates are committed under:

- `config_templates/basic_engine_v0_settings.csv`
- `config_templates/basic_engine_v0_routes.csv`

The runtime copies belong in the configured `CSV_DIR`.

### Read-only safety boundary

The EVM V2 adapter:

- calls `getAmountsOut` for the exact route;
- compares the full-size quote with a small reference quote to estimate size impact;
- estimates gas when a public `simulation_from` address can do so;
- otherwise uses the CSV fallback gas units for quote accounting but refuses to claim a successful simulation;
- checks wrapped-token balance and router allowance for the public simulation address;
- calls `swapExactTokensForTokens` through `eth_call` only;
- sets an output floor covering input + estimated gas + safety buffer + minimum profit;
- never accepts or reads a private key;
- contains no transaction-signing method;
- contains no `send_raw_transaction` path;
- uses a `NoBroadcastExecutor` sentinel even though the core execution switch is already forced off by the CSV factory.

A real dry-run can therefore reach `DRY_RUN_READY` only after a second fresh quote and second successful `eth_call`, but it still cannot broadcast.

## What v0 contains

- strategy-neutral `Candidate` objects;
- a pluggable `CandidateSource`;
- a pluggable `Quoter`;
- ordered, fail-closed `RiskGate` plugins;
- a pluggable exact `Simulator`;
- one serial `Executor` interface;
- optional observers for monitoring/Telegram/logging;
- two-pass quote and simulation before execution;
- rejection fall-through so one bad candidate does not starve the cycle;
- dry-run mode as the default;
- the `atomic_arbitrage` source and economics gate;
- CSV configuration loading;
- a read-only EVM V2 quote/gas/`eth_call` adapter;
- a no-broadcast executor sentinel.

## What v0 intentionally does NOT contain yet

- private-key loading;
- transaction signing;
- transaction broadcasting;
- nonce submission;
- Solana adapters;
- V3 adapters;
- stablecoin-denominated gas conversion/oracles;
- pool-rug APIs;
- Telegram wiring;
- AI agents;
- strategy factory wiring;
- production CLI/service wiring;
- live deployment wiring.

Those are future adapters/plugins, not reasons to complicate the core engine.

## Upgrade path

Add capabilities in layers without changing the core contract.

### Layer 1 — chain adapters

- EVM V2 read-only quote/simulation: implemented;
- EVM V3 read-only quote/simulation: future;
- Solana read-only quote/simulation: future;
- signing/broadcast adapter: future and separately gated.

### Layer 2 — market discovery

- legacy v2.2 direct-market scanner adapter;
- current V2/V3/full-power scanner adapters;
- leader/copy-trading candidate adapter;
- additional arbitrage candidate adapters.

All scanners feed the same candidate interface. No scanner gets signing authority.

### Layer 3 — safety plugins

Examples:

- pool-rug/liquidity gate;
- honeypot/sellability gate;
- product-universe gate;
- execution-quarantine gate;
- price-impact/slippage gate;
- wallet balance/allowance gate;
- exposure/capital limits.

A plugin may reject a candidate but cannot execute it.

### Layer 4 — execution policy

- LIVE / ARMED controls;
- minimum profit policy;
- gas-multiple policy;
- canary sizing;
- per-chain capital limits;
- nonce / blockhash management.

### Layer 5 — observability and control

- Telegram dashboard;
- engineering monitor;
- strategy monitor;
- strategy factory;
- AI-agent recommendations;
- audit trail and rejection counters.

Observers receive engine events but cannot bypass gates.

## Design rule

**Many discovery workers, one execution arbiter.**

This prevents one scanner mode from accidentally becoming the only effective execution path, while also avoiding duplicate signing and nonce races.

## Testing rule

Before any future LIVE integration, replay historical successful opportunities through:

`legacy candidate -> new quote -> every current risk gate -> simulation -> dry-run decision`

Record the first rejection reason. That produces a measurable migration path instead of blindly disabling newer safeguards.
