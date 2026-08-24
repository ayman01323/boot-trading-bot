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
- dry-run mode as the default.

## What v0 intentionally does NOT contain

- RPC clients;
- wallets or private keys;
- transaction signing;
- broadcasting;
- EVM or Solana implementation details;
- DEX-specific code;
- pool-rug APIs;
- Telegram;
- AI agents;
- strategy factory;
- live deployment wiring.

Those are adapters/plugins, not core-engine responsibilities.

## Upgrade path

Add capabilities in layers without changing the core contract:

### Layer 1 — chain adapters

- EVM quote/simulation/execution adapter;
- Solana quote/simulation/execution adapter;
- chain-specific gas/fee normalisation.

### Layer 2 — market discovery

- legacy v2.2 direct-market scanner adapter;
- current V2/V3/full-power scanner adapters;
- leader/copy-trading candidate adapter;
- arbitrage candidate adapter.

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

This prevents the current class of failure where one scanner mode accidentally becomes the sole execution owner, while also avoiding duplicate signing and nonce races.

## Testing rule

Before any future LIVE integration, replay historical successful opportunities through:

`legacy candidate -> new quote -> every current risk gate -> simulation -> dry-run decision`

Record the first rejection reason. That produces a measurable migration path instead of blindly disabling newer safeguards.
