# claude-trading-bot

A second, isolated deployment of the existing `learnerbot` trading engine — operated
separately from the production bot, reporting to its own Telegram destination, with
its own wallet, config, and state. It **runs the same audited strategy and risk code
as production**, not a reimplementation, so it cannot silently drift from the
hardened safety gates that already exist in `learnerbot/`.

## Why "run the same code" instead of "port the logic"

The strategy/risk logic in `learnerbot/` (leader-quality gates, drawdown guards,
pool/token safety checks, execution engines) is composed from ~90 patch modules
applied in a specific, integrity-checked import order (see
`learnerbot/__main__.py`, `learnerbot/trading_runtime_invariant_patch.py`,
`learnerbot/final_runtime_integrity_patch.py`). Hand-porting the resolved threshold
values into new code risks getting the "hard floor" wrong and quietly weakening a
protection that took real incidents to establish. Running the actual package again,
pointed at isolated config/state, guarantees the same gates apply with zero
transcription risk.

Everything in this folder is a **thin isolation and identity layer** around the
existing package, not a rewrite of trading logic.

## Isolation design

`learnerbot/config.py` already supports pointing one codebase at a separate config
and state root via `CSV_DIR` / `DATA_DIR` env vars — that's the seam this bot uses.
Wallet stores (`wallet_store.py`, `solana_wallet_store.py`, `multi_wallet_store.py`)
key their encrypted keyring off `DATA_DIR`, so a distinct `DATA_DIR` automatically
gives a distinct, non-overlapping wallet keyring — no code changes needed for that.

**Hazard this design has to defend against:** `learnerbot/config.py` calls
`load_dotenv(BOT_ROOT / ".env")` where `BOT_ROOT` is wherever the `learnerbot`
package physically lives on disk — i.e. the *production* bot's own `.env`, since
this folder currently reuses the same package install (see "Known limitation:
separate checkout" below). `load_dotenv` does not override variables already
present in the process environment, so as long as every sensitive variable this
bot needs is explicitly set before `learnerbot` is imported, nothing falls through
to production values. [`run.py`](run.py) enforces this: it loads *only*
`claude-trading-bot/.env`, checks every required variable is present, and refuses
to start (fail closed) if any are missing — it never silently inherits a
production Telegram token, chat id, or wallet secret.

## What's reused as-is vs. new in this folder

**Reused, unmodified:** `learnerbot.config.AppSettings`, the SQLite schema/atomic
CSV write helpers, `learnerbot.rpc.RPCClient`, `evm_pool_rug_gate.py` /
`solana_pool_risk_gate.py` (token/pool safety), `live_executor.LiveTrader` /
`solana_live_executor.SolanaLiveExecutor` (quote → simulate → sign → broadcast →
post-validate), the encrypted wallet stores, `learnerbot.telegram` (API wrapper),
and the full CSV-backed kill-switch chain (`engine_enabled`, `trading_enabled`,
per-user `live_trading_enabled`/`auto_trading_enabled`, all defaulting OFF).

**New in this folder:**
- [`run.py`](run.py) — fail-closed env isolation + entrypoint.
- [`risk_engine_guard.py`](risk_engine_guard.py) — an *additional* hard-limit layer
  (capital/exposure/position caps) that sits in front of the reused execution
  engines. It only ever adds a tighter check; it cannot loosen anything reused
  from `learnerbot`.
- [`identity_patch.py`](identity_patch.py) — prefixes every outgoing Telegram
  message with `🤖 CLAUDE TRADING BOT` and sends the startup status message,
  following this codebase's existing `*_patch.py` convention rather than editing
  `learnerbot/telegram.py` directly.
- [`preflight_check.py`](preflight_check.py) — the non-trading readiness checklist
  (RPC, WebSocket, market data, both-side quotes, pool/token safety dry-run,
  wallet balance read, DB init, Telegram delivery, kill-switch state,
  signer readiness, restart/recovery) — no signing or broadcast occurs in
  this script.
- [`signing_interface.py`](signing_interface.py) — answers exactly one
  question, SIGNER_READY true/false, by checking whether an encrypted
  signing key exists in `learnerbot.solana_wallet_store.SolanaWalletStore`
  (the existing, reviewed keystore — reused, not reimplemented) for this
  instance's isolated wallet-owner id. Until GPT/operator provisions a
  dedicated wallet on the Google server, this reports `false` and every
  caller treats that as broadcast-unavailable. It does not itself decide to
  broadcast anything — that still requires the existing ARMED/LIVE_TRADING
  platform gates plus `risk_engine_guard.py`, unchanged.
- `systemd/claude-trading-bot.service` — a unit template for running this as its
  own process, parallel to `systemd/learnerbot.service`. **Not yet installable**
  — see limitations below.

## Fail-closed defaults

`ARMED` / live trading stays off until explicitly turned on, at every layer:
- The reused platform gates (`live_trading_settings.csv:trading_enabled`,
  `solana_settings.csv` live flag, per-user `live_trading_enabled`) default OFF
  and are unaffected by anything in this folder.
- `risk_engine_guard.py` additionally refuses to start if `MAX_CAPITAL_USD`,
  `MAX_POSITION_USD`, `MAX_TOTAL_EXPOSURE_USD`, `MAX_OPEN_POSITIONS`,
  `MAX_DAILY_LOSS_USD`, `MAX_DRAWDOWN_PCT`, `MAX_SLIPPAGE_PCT`,
  `MAX_PRICE_IMPACT_PCT`, or `MIN_POOL_LIQUIDITY_USD` are absent or invalid.
- `run.py` refuses to start at all if any required identity/config variable is
  missing from its runtime env file (see "Runtime config location" below).

No broadcast happens merely because this is deployed or running — it happens only
when the operator explicitly enables the reused platform LIVE gates *and* the risk
guard's limits are satisfied, per the project's existing `CONFIRM`-word and
verified-write kill-switch conventions.

## Known limitations (reported, not worked around)

1. **Same checkout, not a separate one yet.** This folder currently expects to run
   from within the same `boot-trading-bot` checkout as production (importing the
   installed `learnerbot` package directly). The env-isolation defense above
   makes that safe for *config/secrets*, but it does mean this bot and production
   share a Python environment and dependency versions. A fully separate checkout
   (e.g. a pinned `learnerbot` install under this folder) would remove that
   coupling entirely — flagged as a possible follow-up, not done here to avoid
   duplicating a large, actively-changing package without a clear win yet.
2. **Server path.** The `Claude Google Controlled Operations` sync workflow syncs
   the whole `boot-trading-bot` repo to
   `/home/ayman01323/ClaudeServer/boot-trading-bot/`. This folder's *code* lands
   at `/home/ayman01323/ClaudeServer/boot-trading-bot/claude-trading-bot/`, not a
   separate top-level `/home/ayman01323/ClaudeServer/claude-trading-bot/` path —
   but its *runtime config and state* now correctly live under
   `/home/ayman01323/ClaudeServer/runtime/` instead (see next point), which is a
   real, separate, operator-managed area outside the git checkout entirely.
3. **Runtime config location — do not put CSV_DIR/DATA_DIR inside the checkout.**
   `claude-google-controlled-ops.yml`'s `sync` action refuses to run if
   `git status --porcelain` on the managed checkout shows *any* local changes —
   and that check does not exclude untracked files. If this bot's CSV config or
   SQLite state were written inside `claude-trading-bot/` (even though gitignored),
   every sync after this bot's first run would start failing with "managed server
   workspace has local changes." `env.example` now points `CSV_DIR`/`DATA_DIR` at
   `/home/ayman01323/ClaudeServer/runtime/{CSVbot,data}` instead — outside the
   checkout, alongside the operator-provisioned `rpc_endpoints.csv` and
   `claude-trading-bot.env`. `run.py`'s `DEFAULT_ENV_FILE` reads its own runtime
   config from `/home/ayman01323/ClaudeServer/runtime/claude-trading-bot.env`
   directly (override with `CLAUDE_BOT_ENV_FILE` for local/off-server testing).
3. **No running-service mechanism yet.** The Google sync workflow only performs
   `inspect` / `test` / `sync` against a git checkout — it has no systemd/process
   management and is explicitly barred from restarting production services. The
   `systemd/claude-trading-bot.service` file in this folder is ready, but nothing
   in the current bounded workflow can install or start it. Getting this bot
   actually *running continuously* needs a narrowly-scoped addition to that
   workflow (e.g. a bounded `install-service`/`restart` action limited to this
   one unit) — this is a decision for GPT/the operator, not something to bypass
   by adding broader server access here.
4. **Wallet creation is intentionally not automated here.** Per the project's
   existing pattern, wallet keys are never placed in `.env` or source — a Solana
   wallet is created/imported through the bot's own encrypted wallet-store command
   flow once it's running (keyed to this instance's isolated `DATA_DIR`), the same
   way production wallets are created. `env.example` documents which *secret
   references* (not values) are needed; no private key belongs in this repo.
