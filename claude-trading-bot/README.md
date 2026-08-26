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
`load_dotenv(BOT_ROOT / ".env")` (no `override=True`) where `BOT_ROOT` is
wherever the `learnerbot` package physically lives on disk — i.e. the
*production* bot's own `.env`, since this folder currently reuses the same
package install (see "Known limitation: separate checkout" below).
`load_dotenv` only fills a var that is not already present in `os.environ` at
all — present-but-blank still counts as present — so two layers close this:
(1) [`run.py`](run.py) loads this instance's own runtime env file first, with
`override=True`, checks every required identity/risk variable is present, and
refuses to start (fail closed) if any are missing; (2)
[`claude_bot_quarantine.py`](claude_bot_quarantine.py)'s
`block_production_env_fallback()` additionally blanks every other
secret-shaped variable `learnerbot` might read anywhere in its ~230 files
(found by an actual audit, not just the ones this bot explicitly uses) —
`LIVE_WALLET_PRIVATE_KEY`, `GITHUB_TOKEN`, `OPENAI_API_KEY`, the `HELIUS_*`
vars, `SOLANA_RPC_URLS`/`SOLANA_RPC_FALLBACK_URLS`, `SOLANA_EXPLORER_URL` —
before `learnerbot` is ever imported, so none of them can silently inherit a
production value either. `verify_bootstrap_composition.py` checks this stayed
true after a full chain run, not just at the moment it's set.

This is an audited, enumerated list, not a mathematical guarantee against
every conceivable current or future `os.getenv` call anywhere in `learnerbot`
— if a new secret-shaped env var is added to that package later without a
corresponding entry in `claude_bot_quarantine._PRODUCTION_ONLY_SECRETS`, it
could fall through until the audit is redone. Stated precisely rather than
claimed absolutely, per review.

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
- [`claude_bot_quarantine.py`](claude_bot_quarantine.py) — replaces every
  known historical production migration in `learnerbot`'s patch chain (12
  found by audit — see the module's own docstring for the full list and why
  each is included or, in two cases, deliberately excluded) with an empty
  stand-in module in `sys.modules`, so their code never executes at all —
  zero repo-root writes, not "writes limited to a marker file" (an earlier
  version of this module worked by pre-creating those migrations' own
  marker files so they'd see themselves as already-applied; review correctly
  rejected that as still violating a zero-repo-root-write invariant, since
  creating the marker is itself a write). Also blanks every secret-shaped
  env var this bot doesn't need so `learnerbot/config.py`'s un-overridden
  `load_dotenv(BOT_ROOT/.env)` can never silently fill one in from
  production's real `.env`. Both must run before the *first* `learnerbot`
  import in a process — enforced by
  `quarantine_before_any_learnerbot_import()` raising if called too late —
  and both `run.py` (parent) and `bootstrap_run.py` (child; `os.execvpe()`
  gives it a fresh interpreter with empty `sys.modules`, so the parent
  having done this doesn't cover the child) call it first, before anything
  else. Not hypothetical: a real test run before this module existed proved
  `telegram_account_roles_patch.py` and `polygon_live_enable_migration.py`
  both replay against a fresh instance — see "Known limitations" and
  `verify_bootstrap_composition.py`, which actively checks (not just
  trusts) that quarantine actually worked, requiring zero repo-root file
  changes with no exception.
- [`evm_execution_guard_patch.py`](evm_execution_guard_patch.py) — wraps
  every `LiveTrader` signing/broadcast entry point (`buy`, `sell`,
  `execute_cycle`, `execute_v3_cycle`) to unconditionally refuse. EVM has no
  execution guard with the same properties as Solana's yet (identity check,
  `SIGNER_READY`, risk limits), so no `AUTHORISED_CHAINS` value can make an
  EVM chain actually tradeable through this bot — that variable only
  authorises a chain that also has a real guard enforcing something, and
  none exists for EVM. This is what "fail closed" means here: refusal is
  unconditional, not merely default-off.
- [`bootstrap_run.py`](bootstrap_run.py) — the actual exec target `run.py` hands
  off to (see "Why bootstrap_run.py exists" below). Calls
  `claude_bot_patches.install_all()` in the child process before running
  `learnerbot` exactly the way `python -m learnerbot run` would.
- [`claude_bot_patches.py`](claude_bot_patches.py) — the single list of what
  must be installed before `learnerbot`'s own patch chain runs. Imported by
  both `bootstrap_run.py` and `verify_bootstrap_composition.py` so the real
  exec path and the test that proves it can never silently diverge.
- [`verify_bootstrap_composition.py`](verify_bootstrap_composition.py) — a
  non-broadcast test proving two things end to end: (1) the Claude guard
  survives `learnerbot/__main__.py`'s complete ~60-module patch chain (run
  for real via `runpy.run_module`, argv forced to the harmless `chains`
  subcommand so the chain executes without ever starting the trading loop),
  and (2) `SIGNER_READY=false` and a mismatched runtime identity both refuse
  *before* reaching the real executor — proven by monkeypatching the
  underlying buy/sell to a sentinel that fails loudly if ever called, then
  confirming the guard raises first in both cases. No transaction is
  constructed, signed, or broadcast by this script.
- [`risk_engine_guard.py`](risk_engine_guard.py) — pure config validation plus
  `check_new_position()` (capital/exposure/position caps) and
  `check_daily_loss_and_drawdown()`. The latter is a running-peak drawdown
  over this instance's own *closed*-position history only — realized P&L,
  not mark-to-market: an open position's unrealized loss does not count
  toward `MAX_DRAWDOWN_PCT` until it's actually closed. Documented as an
  approximation, not hidden as one — see the function's own docstring and
  `solana_execution_risk_patch.py`'s query functions for exactly what's
  measured.
  By itself this file only validates config and holds pure functions;
  [`solana_execution_risk_patch.py`](solana_execution_risk_patch.py) is what
  actually calls them before every Solana LIVE buy — see below for why these
  are separate files. Does **not** cover slippage, price-impact, or minimum
  liquidity — see the module's own docstring for exactly which reused,
  already-reviewed `learnerbot` code governs those instead; a second
  Claude-specific implementation risked being redundant or inconsistent with
  logic already reviewed.
- [`solana_execution_risk_patch.py`](solana_execution_risk_patch.py) — wraps
  both `SolanaLiveExecutor.buy` and `.sell` (the real signing/broadcast entry
  points). Every call checks the *runtime* identity the executor was actually
  constructed with against `CLAUDE_BOT_WALLET_OWNER_ID` and `SIGNER_READY`
  (closes a gap review found: a mismatched identity with its own key could
  otherwise reach the executor even though `SIGNER_READY` described a
  different wallet). Buys additionally check `AUTHORISED_CHAINS` (fails
  closed — no chain authorised by default) and
  `risk_engine_guard`'s position/exposure/daily-loss/drawdown limits, priced
  via a live Jupiter quote and this instance's own closed-position history.
  Sells skip the chain/risk checks deliberately — closing a position reduces
  risk, and revoking authorisation or hitting a cap mid-position shouldn't
  trap capital in a position this bot can no longer exit. Only tightens;
  cannot loosen anything reused from `learnerbot`.
  EVM is not wired here — no equivalent guard exists for `LiveTrader`, and
  `AUTHORISED_CHAINS` defaults to nothing authorised regardless, so EVM
  cannot execute through this bot yet even where its RPCs are reachable (see
  Known limitations for current EVM connectivity, which is partial, not
  total, failure).
- [`identity_patch.py`](identity_patch.py) — prefixes every outgoing Telegram
  message with `🤖 CLAUDE TRADING BOT` and sends the startup status message,
  following this codebase's existing `*_patch.py` convention rather than editing
  `learnerbot/telegram.py` directly.

**Why `bootstrap_run.py` exists, not just `python -m learnerbot run`:**
`os.execvpe()` replaces the process image entirely — any monkey-patching done
in `run.py`'s process before exec (identity prefix, risk guard) is gone in a
freshly-exec'd `python -m learnerbot run`, since that starts a brand new
interpreter with no memory of what the parent process did. Earlier versions of
this folder called `os.execvpe` directly on `-m learnerbot run` and both
patches silently only affected the one startup message sent before exec, never
the actual trading loop — caught in review before anything was armed.
`bootstrap_run.py` is the fix: it *is* the exec target, installs both patches
first, then runs `learnerbot` via `runpy.run_module(..., run_name="__main__")`
— functionally identical to `-m learnerbot run`, just with the patches
already active before `learnerbot/__main__.py`'s own chain begins.
- [`preflight_check.py`](preflight_check.py) — the non-trading readiness checklist
  (RPC, WebSocket, market data, both-side quotes, pool/token safety dry-run,
  wallet balance read, DB init, Telegram delivery, kill-switch state,
  signer readiness, restart/recovery) — no signing or broadcast occurs in
  this script.
- [`signing_interface.py`](signing_interface.py) — answers exactly one
  question, SIGNER_READY true/false, by checking whether an encrypted
  signing key exists in `learnerbot.solana_wallet_store.SolanaWalletStore`
  (the existing, reviewed keystore — reused, not reimplemented) for this
  instance's isolated wallet-owner id. Also verifies — not just assumes —
  that `CLAUDE_BOT_WALLET_OWNER_ID` is actually the identity execution will
  use: `solana_live_patch.process_leader_event()` builds its executor from
  whatever `learnerbot.user_registry.all_users()` yields, a value this module
  doesn't otherwise control, so it fails closed unless this instance has
  exactly one enabled user and that user's `telegram_id` matches the owner id
  (caught in review — without this, SIGNER_READY could describe a different
  wallet than the one execution actually uses). Until GPT/operator provisions
  a dedicated wallet on the Google server, this reports `false` and every
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
  `MAX_DAILY_LOSS_USD`, or `MAX_DRAWDOWN_PCT` are absent or invalid — and
  `solana_execution_risk_patch.py` actually checks the daily-loss and
  drawdown values (computed from this instance's own closed-position
  history, priced via a live Jupiter quote) before every guarded buy, not
  just at startup. Slippage, price-impact, and minimum-liquidity are
  intentionally not part of this contract — see `risk_engine_guard.py`'s
  module docstring for exactly which reused, already-reviewed `learnerbot`
  code governs each instead.
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
   **Update:** a real Google-server preflight run found `CSV_DIR`/`DATA_DIR`
   simply left blank in the operator's runtime file — meaning correctness here
   depended entirely on a manual step that was never actually taken.
   `run.py`'s `_apply_deterministic_runtime_dir_defaults()` now fills both in
   automatically from `DEFAULT_RUNTIME_DIR` (the same directory
   `DEFAULT_ENV_FILE` lives in, structurally guaranteed outside the checkout)
   whenever they're unset, so the common case needs no manual entry at all —
   `env.example` now ships them blank. An explicit value in the operator's own
   env file still always wins over the default.
4. **No running-service mechanism yet.** The Google sync workflow only performs
   `inspect` / `test` / `sync` against a git checkout — it has no systemd/process
   management and is explicitly barred from restarting production services. The
   `systemd/claude-trading-bot.service` file in this folder is ready, but nothing
   in the current bounded workflow can install or start it. Getting this bot
   actually *running continuously* needs a narrowly-scoped addition to that
   workflow (e.g. a bounded `install-service`/`restart` action limited to this
   one unit) — this is a decision for GPT/the operator, not something to bypass
   by adding broader server access here.
5. **Wallet creation is intentionally not automated here.** Per the project's
   existing pattern, wallet keys are never placed in `.env` or source — a Solana
   wallet is created/imported through the bot's own encrypted wallet-store command
   flow once it's running (keyed to this instance's isolated `DATA_DIR`), the same
   way production wallets are created. `env.example` documents which *secret
   references* (not values) are needed; no private key belongs in this repo.
6. **EVM connectivity is partial, not total, failure — and not wired for
   execution regardless.** `diagnostics/claude-google-runtime-check.txt` on
   `server-diagnostics` (as of the latest botgoogle run) shows Ethereum 1/2
   endpoints PASS and BSC 2/3 PASS; Polygon, Base, and Arbitrum are 0/2 with
   HTTP 403/429 (rate-limit/auth issues on those specific providers, not a
   network-wide problem — Solana/Jupiter connectivity from the same runner is
   fully healthy). Regardless of endpoint health, no EVM chain can currently
   execute through this bot: `AUTHORISED_CHAINS` defaults to nothing
   authorised, and no execution guard equivalent to
   `solana_execution_risk_patch.py` exists yet for `LiveTrader`. There is also
   a real path mismatch to resolve before EVM config would even load: the
   operator-provisioned `rpc_endpoints.csv` lives flat at
   `/home/ayman01323/ClaudeServer/runtime/rpc_endpoints.csv`, while
   `learnerbot/config.py::load_chains` reads it from `CSV_DIR/rpc_endpoints.csv`
   — and `CSV_DIR` is `.../runtime/CSVbot`, a different directory. See
   `env.example`'s EVM section for detail; not resolved here since the fix
   (move the file, symlink it, or designate an explicit alternate path) is an
   operator decision, not something to route around in code.
7. **Operator identity: the original problem is now fixed by quarantine, not
   just documented.** Discovered by actually running
   `verify_bootstrap_composition.py` against `learnerbot`'s complete patch
   chain (not by inspection): `telegram_account_roles_patch.py`, a
   marker-gated one-time migration meant for the production deployment,
   replayed against a fresh `DATA_DIR` lacking its marker file — including
   this isolated instance's — and created its own hardcoded user row
   independent of `TELEGRAM_CHAT_IDS`. `claude_bot_quarantine.py` now
   replaces that migration (and 11 others found by a systematic audit — see
   that module's docstring) with an empty stand-in in `sys.modules` before
   `learnerbot`'s chain ever imports it, so its code never runs at all and
   this specific replay no longer happens.
   `verify_bootstrap_composition.py` actively re-checks this every run
   rather than trusting the fix once made. `CLAUDE_BOT_WALLET_OWNER_ID`
   should now correctly equal `TELEGRAM_CHAT_IDS[0]` via
   `ensure_master_seed()`, as originally intended — but the quarantine list
   is audit-based, not proven exhaustive for all future `learnerbot`
   changes, so `signing_interface.py`'s identity-consistency check stays in
   place as the real safety net regardless: it fails closed on any mismatch,
   quarantined or not.
