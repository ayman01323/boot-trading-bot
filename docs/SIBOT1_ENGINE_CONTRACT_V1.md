# SiBot 1 Engine Contract v1

Status: design contract / inert scaffolding. This file does **not** enable trading.

## 1. Location

Production repository root remains `/root/multichain-learning-bot-v2.2-fast-direct-market/`.

SiBot 1 code lives at:

- `sibot1_engines/` — independent engine code.
- `CSVbot/sibot1/` — shared SiBot 1 CSV configuration plus per-engine CSV settings.
- `data/sibot1/` — runtime state, evidence, accounting and results.

The existing MAIN BOOT remains outside SiBot 1. `learnerbot/basic_engine_v0/` is an older GPT test engine and is not the SiBot 1 platform.

## 2. Ownership boundary

GPT owns and reviews shared platform code under `sibot1_engines/_shared/` and all bridges to PoolCheck, capital, wallet, nonce and execution infrastructure.

Each engine author owns only `sibot1_engines/<engine_id>/`, its own tests, and its own settings schema. Initial engine IDs are `gpt`, `claude`, `gemini`, `deepseek`, `grok`, `kimi`, `copilot`; additional AI or non-AI engines can be registered later.

An engine MUST NOT:

- sign or broadcast a transaction directly;
- read a private key, seed phrase or raw signer secret;
- reserve physical wallet funds itself;
- mutate another engine's settings/state/results;
- sell another engine's attributed lot;
- bypass central PoolCheck / rug protection;
- bypass emergency stop, signer integrity, nonce/reconciliation or transaction-integrity controls;
- modify the existing MAIN BOOT unless a separate GPT-reviewed integration change is approved.

## 3. Free-hand strategy rule

Each engine has free hand over its own trading logic and may independently choose chain, DEX/venue, market universe, entry logic, exit logic, trade sizing recommendation, profit target, stop logic, holding period, cadence, indicators, learning method and RPC preference.

Those strategy controls belong in the engine's own CSV/settings and code. They are not central SiBot 1 strategy limits.

The only common trading-risk veto is the central PoolCheck / rug-pool protection layer. System emergency stop and execution-integrity controls are also always authoritative.

## 4. Shared Data Hub

All engines consume normalized data through the Shared Data Hub contract. Engines may request dedicated RPC/WS capacity when their strategy needs it, but may not embed provider secrets in code or CSV.

`CSVbot/sibot1/rpc_registry.csv` supports SHARED, DEDICATED and HYBRID allocation using secret references only.

The hub should normalize timestamps, chain identity, source age, pool/token IDs, prices, liquidity, volume and quote metadata. Shared subscriptions/cache should be reused where practical to avoid seven duplicate RPC/API calls.

## 5. Engine interface

Every engine must expose a factory `build_engine(settings_path, runtime_dir)` returning an object that implements the protocol in `sibot1_engines._shared.contracts`.

Minimum flow:

`MarketEvent -> engine.on_market_event() -> TradeIntent | ExitIntent | None`

The engine owns strategy decisions only. It does not own capital reservation, PoolCheck verdicts, signing or broadcast.

## 6. Trade intent

A BUY/ENTER intent must include engine identity/version, chain, venue/route, input/output asset, requested input amount, strategy/signal identifiers, market timestamp and optional expected economics/metadata.

After an intent is emitted, the shared platform performs:

1. capital reservation against the engine virtual sub-account;
2. mandatory PoolCheck using the proposed exact position size;
3. fresh quote/requote as required;
4. simulation/preflight;
5. wallet/nonce/signing integrity;
6. broadcast only if all shared gates pass;
7. receipt/reconciliation;
8. creation of an engine-owned position lot.

## 7. One-wallet funding model

One physical wallet may be shared by many engines. The wallet balance is **not** the strategy ledger.

The shared Capital Manager keeps virtual sub-accounts. Example: a 1,000 USDC wallet may allocate virtual balances to GPT, Claude, Gemini, DeepSeek, Grok, Kimi and a reserve. Before a trade, an engine requests an amount; the Capital Manager atomically reserves available virtual balance so two engines cannot spend the same physical funds.

Reservation lifecycle:

`AVAILABLE -> RESERVED -> SPENT/OPEN_POSITION -> RELEASED/SETTLED`

Physical wallet balance, engine virtual balance, reserved balance and open-position cost basis must reconcile after every transaction.

## 8. Position ownership and exits

Every successful entry creates an immutable attributed lot containing at minimum `lot_id`, `engine_id`, `strategy_version`, `chain`, `asset`, quantity, cost basis, entry transaction, entry timestamp and remaining quantity.

An engine exit request identifies a specific `lot_id` or asks to close a quantity that the Position Manager maps only to that engine's lots. An engine cannot sell more than its owned attributed quantity even if the physical wallet owns more of the same token.

If GPT owns 100 ABC and Gemini owns 60 ABC in the same wallet, the physical wallet may show 160 ABC, but GPT may exit at most its remaining 100 ABC and Gemini at most its remaining 60 ABC.

Central emergency PoolCheck / rug-protection exit is the only ownership-independent safety path: it may close affected physical exposure for safety, but settlement must still allocate fills/P&L back to the underlying engine lots.

## 9. Same-token opposite intents

Version 1 does not internally cross or net one engine's BUY against another engine's SELL. The shared executor serializes conflicting wallet+chain+asset mutations, executes them through the real venue, and preserves exact attribution. Internal crossing can be added only after a fair-transfer-price/accounting design exists.

## 10. EVM nonce and Solana execution

All EVM transactions from one physical address share one nonce sequence. The shared Nonce Manager is therefore the only nonce authority. Engines may run, quote and decide in parallel, but signed EVM transactions are coordinated centrally.

Solana has different transaction semantics, but the same central wallet ownership and accounting boundary remains mandatory.

## 11. Per-engine storage

Engine code: `sibot1_engines/<engine_id>/`

Engine settings: `CSVbot/sibot1/engines/<engine_id>/settings.csv`

Engine runtime: `data/sibot1/engines/<engine_id>/`

Shared runtime/cache: `data/sibot1/shared/`

Engines must not write directly to another engine's directory.

## 12. Required engine package

Each agent PR must include, at minimum:

- `sibot1_engines/<engine_id>/__init__.py`
- `sibot1_engines/<engine_id>/engine.py`
- `sibot1_engines/<engine_id>/strategy.py` (or documented equivalent)
- `sibot1_engines/<engine_id>/settings_schema.py`
- `CSVbot/sibot1/engines/<engine_id>/settings.example.csv`
- `tests/sibot1/<engine_id>/...`
- `docs/sibot1/<engine_id>_FLOW.md`

Each PR must document chain/DEX, data requirements, entry, exit, learning loop, requested RPC mode, expected cost, failure modes and telemetry.

## 13. Required tests for each engine

Agent tests must prove:

- importing/building the engine does not access a signer;
- no direct transaction broadcast path exists in engine code;
- the engine accepts normalized MarketEvent input;
- candidate generation is deterministic for fixed test inputs unless randomness is explicitly seeded/documented;
- exit decisions cannot name another engine as owner;
- missing/stale mandatory data returns no trade rather than inventing data;
- settings are isolated to the engine;
- all emitted TradeIntent/ExitIntent objects validate against the shared contract.

Shared GPT integration tests will separately prove capital reservation, lot ownership, PoolCheck authority, nonce serialization, reconciliation and emergency exit attribution.

## 14. GitHub workflow for independent agents

Each agent works from the common contract base and uses its own branch:

- `sibot1/engine-gpt-v1`
- `sibot1/engine-claude-v1`
- `sibot1/engine-gemini-v1`
- `sibot1/engine-deepseek-v1`
- `sibot1/engine-grok-v1`
- `sibot1/engine-kimi-v1`
- `sibot1/engine-copilot-v1`

The agent should commit only its allowed folder(s), tests, settings example and flow document. It should open a PR to the common SiBot 1 integration base. If an agent environment cannot write GitHub, it must return a complete patch/file bundle; GPT Controller will upload it unchanged to that agent's branch and clearly record that GPT performed transport only.

No agent PR may merge itself. GPT reviews cross-engine compatibility, tests and safety boundaries before any merge.

## 15. Deployment sequence

1. merge inert common contract/platform scaffolding;
2. merge independent engine PRs only after tests;
3. integrate shared Data Hub/Capital Manager/Position Manager/PoolCheck bridge/execution adapter;
4. deploy services disabled / no LIVE side effects;
5. run deterministic and SHADOW/integration tests;
6. configure wallet/allocations/RPC references;
7. explicit operator action is required before real-fund execution.

Deployment of code must not silently enable trading.
