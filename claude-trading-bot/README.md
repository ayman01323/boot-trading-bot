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
- [`risk_engine_guard.py`](risk_engine_guard.py) — the single source of truth
  for risk numbers. Consolidated per direct owner instruction (2026-08-26):
  position size, aggregate exposure, open-position count, and drawdown are
  now owner-approved fixed constants (`OWNER_MAX_OPEN_POSITIONS=10`,
  `OWNER_MAX_POSITION_PCT=3.00%`, `OWNER_MAX_TOTAL_EXPOSURE_PCT=30.00%`,
  `OWNER_MAX_DRAWDOWN_PCT=20.00%`), calculated dynamically against exactly
  one operator-provided number, `CLAUDE_CAPITAL_BASIS_USD` — not five
  independent dollar/percent knobs an operator could misconfigure or leave
  inconsistent with each other. `drawdown_pct()` is the one place drawdown
  percentage is computed; every caller (execution guard, `/claude_status`,
  tests) goes through it. Drawdown itself is a running-peak measurement over
  this instance's own *closed*-position history only — realized P&L, not
  mark-to-market: an open position's unrealized loss does not count toward
  the 20% latch until it's actually closed. Documented as an approximation,
  not hidden as one. Does **not** cover slippage, price-impact, or minimum
  liquidity — see the module's own docstring for exactly which reused,
  already-reviewed `learnerbot` code governs those instead.
- [`claude_state.py`](claude_state.py) — the one authoritative state machine:
  ordinary operating state (`OFF`/`ARMED`/`STOPPING`, reset to `OFF` on every
  restart, never auto-restored to `ARMED`) kept deliberately separate from
  the persistent `HALTED_DRAWDOWN` safety latch (survives restart, crash,
  reboot, config reload, deployment/sync — cleared only through the
  two-step, single-use-challenge owner restart flow). Both persist in one
  atomically-written JSON file under this instance's own `DATA_DIR`.
- [`telegram_control_patch.py`](telegram_control_patch.py) — the one
  authoritative Telegram command router (`/claude_status`,
  `/claude_arm_live CONFIRM`, `/claude_disarm`, `/claude_stop`,
  `/claude_restart_request`, `/claude_restart_confirm CONFIRM` — see "Telegram
  control" below). Owner-only, wraps `learnerbot.telegram_ui.handle_update`
  once; replaces an earlier ad-hoc `/sibot1riskresume` handler that used to
  live inside `solana_execution_risk_patch.py` (misnamed after the unrelated
  production SiBot, and a second competing patch onto the same hook — both
  fixed by consolidating into this one file).
- [`solana_execution_risk_patch.py`](solana_execution_risk_patch.py) — wraps
  both `SolanaLiveExecutor.buy` and `.sell` (the real signing/broadcast entry
  points). Every call checks the *runtime* identity the executor was actually
  constructed with against `CLAUDE_BOT_WALLET_OWNER_ID` and `SIGNER_READY`
  (closes a gap review found: a mismatched identity with its own key could
  otherwise reach the executor even though `SIGNER_READY` described a
  different wallet). Buys additionally check `AUTHORISED_CHAINS` (fails
  closed — no chain authorised by default), `claude_state`'s operating state
  (must be `ARMED`, must not be `HALTED_DRAWDOWN`) and
  `risk_engine_guard`'s position/exposure/drawdown limits, priced via a live
  Jupiter quote and this instance's own closed-position history. A drawdown
  breach latches `HALTED_DRAWDOWN` (via `claude_state.latch_drawdown()`) and
  sends the one-time owner alert before refusing. Sells skip the
  state/chain/risk checks deliberately — closing a position reduces risk,
  and revoking authorisation or hitting a cap mid-position shouldn't trap
  capital in a position this bot can no longer exit. Only tightens; cannot
  loosen anything reused from `learnerbot`.
  EVM is not wired here — no equivalent guard exists for `LiveTrader`, and
  `AUTHORISED_CHAINS` defaults to nothing authorised regardless, so EVM
  cannot execute through this bot yet even where its RPCs are reachable (see
  Known limitations for current EVM connectivity, which is partial, not
  total, failure).
- [`identity_patch.py`](identity_patch.py) — prefixes every outgoing Telegram
  message with `🤖 CLAUDE TRADING BOT` and sends the startup status message,
  following this codebase's existing `*_patch.py` convention rather than editing
  `learnerbot/telegram.py` directly.
- [`telegram_connectivity_test.py`](telegram_connectivity_test.py) — a
  one-time, marker-gated connectivity/format proof for THIS isolated
  instance's own token, triggered only by an explicit human operator running
  `python run.py send-test-telegram`. Never auto-installed into any patch
  chain and never called from `claude_bot_patches.install_all()`. Replaces
  an earlier version (`learnerbot/telegram_claude_smoke_patch.py`, removed
  2026-08-26) that lived inside the shared production package with no
  environment gate — it fired identically whichever process imported it,
  meaning it could have sent a real message through **production's own**
  Telegram bot token on production's own next restart. See that file's
  removal note in `learnerbot/final_runtime_integrity_patch.py` for the full
  finding.

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
- `risk_engine_guard.py` additionally refuses to start if
  `CLAUDE_CAPITAL_BASIS_USD` is absent or invalid — position size (3.00%),
  aggregate exposure (30.00%), open-position count (10), and drawdown
  (20.00%) are owner-approved fixed constants derived from that one number,
  not independently configurable. `solana_execution_risk_patch.py` actually
  checks all of them (computed from this instance's own closed-position
  history, priced via a live Jupiter quote) before every guarded buy, not
  just at startup, and additionally requires `claude_state`'s operating
  state to be `ARMED` and not `HALTED_DRAWDOWN`. Slippage, price-impact, and
  minimum-liquidity are intentionally not part of this contract — see
  `risk_engine_guard.py`'s module docstring for exactly which reused,
  already-reviewed `learnerbot`
  code governs each instead.
- `run.py` refuses to start at all if any required identity/config variable is
  missing from its runtime env file (see "Runtime config location" below).

No broadcast happens merely because this is deployed or running — it happens only
when the operator explicitly enables the reused platform LIVE gates *and* the risk
guard's limits are satisfied, per the project's existing `CONFIRM`-word and
verified-write kill-switch conventions.

## Telegram control (2026-08-26, revised 2026-08-26 after review)

State model (`claude_state.py`), two kinds of state kept deliberately separate:
- Ordinary operating state: `OFF` → `ARMED` → `STOPPING` → `OFF`. Resets to
  `OFF` on every process/service restart. `ARMED` is never auto-restored.
- Persistent safety latch: `HALTED_DRAWDOWN`. Survives restart, crash,
  reboot, config reload, and deployment/sync. Never clears on its own —
  not on equity recovery, not on time elapsed, not for an AI agent, a
  mailbox message, an API call, or a scheduler. Only the two-step owner
  restart flow below can clear it.

**Drawdown/equity model** (`claude_state.evaluate_drawdown()`, the one
authoritative function — status, the periodic monitor, the pre-buy check,
and the post-sell recheck all call this, never re-derive it independently).
An earlier version computed drawdown from closed-position realised P&L
only, against the fixed capital basis — review (2026-08-26) correctly
rejected that: it missed unrealised (open-position) losses entirely and
mixed a fixed USD basis against a SOL amount re-priced at read time. The
current model:
- `current_equity_usd` = capital basis + a running `cumulative_realized_pnl_usd`
  total (each *closed position* accounted exactly once, in USD, at the
  price current when it was accounted — see `claude_state.account_closed_position()`
  and its only caller, `solana_execution_risk_patch.reconcile_realized_pnl()`
  — never re-derived later from a different day's price) + today's
  mark-to-market of open positions (`unrealised_net_sol`, a column
  learnerbot's own scanner loop already maintains, reused not
  re-implemented, priced once at read time since it's inherently a "now"
  value).
- **Crash-safe, idempotent accounting** (review, 2026-08-26): an earlier
  version took a before/after `SUM(realised_net_sol)` snapshot around a
  single sell call — a real crash window existed if the process died after
  the sell committed to the positions DB but before the USD delta was
  persisted, permanently losing that P&L from Claude's accounting.
  `reconcile_realized_pnl()` instead asks "which of this instance's closed
  positions, by `position_id`, does the ledger (`accounted_position_ids` in
  `claude_bot_state.json`) not have yet" — called from three places (right
  after every sell, every monitor tick, and once at process startup via
  `claude_state.install()`'s `_app` wrapper) so it always converges to the
  same complete, no-double-counted total no matter when a crash happened.
  `account_closed_position()` is itself idempotent per `position_id`: once
  written, an entry's `pnl_usd`/`price_usd_used` is immutable, never
  recomputed even if reconciliation runs again later at a different price.
- **Two-tier accounting** (strengthened per review, 2026-08-26, correlation
  corrected 2026-08-26): the learnerbot positions schema has no close-time
  USD column, and no price-history table exists anywhere in this codebase
  (checked) — so a close discovered *after the fact* cannot be
  retroactively priced accurately. `_guarded_sell()` diffs the set of
  `CLOSED` position ids for this specific mint, before/after its own call,
  under a per-`(telegram_id, mint)` lock (`_sell_lock_for()`) — `SolanaLiveExecutor.sell()`
  has no execution lock of its own, so an unscoped, unlocked diff could
  have swept a *different*, concurrently-closed position into this call's
  price sample; scoping by mint plus the lock together make the diff exact
  for that mint, while exits of different mints never share a lock and
  proceed independently. `_account_positions_synchronously()` then prices
  whatever this call closed with the SOL/USD rate fetched immediately
  after — close-adjacent, the best available sample, but not claimed to be
  mathematically exact close-time pricing (no timestamped price is
  persisted at the execution boundary itself). `reconcile_realized_pnl()`
  (the generic sweep used by the monitor and at startup) NEVER prices a
  close itself: anything it finds that isn't in either ledger yet is, by
  construction, one the synchronous path never saw (most likely a crash
  between the DB commit and that capture, or a price/accounting failure
  right after a successful sell), so it's recorded in
  `unpriced_closed_position_ids` with no price at all rather than guessed
  at the sweep's own read-time rate — the exact "reprice at today's rate"
  artifact review flagged, now impossible by construction rather than
  merely avoided in the common case. `armed_health_check()` fails closed
  while any unpriced entry remains (below) — that's the resolution path
  (manual reconciliation), not an automatic price guess.
- `high_water_equity_usd` seeds at the capital basis on the first-ever
  measurement, and is otherwise monotonically non-decreasing during normal
  operation. `drawdown_pct = (HWM − current) / HWM × 100`.
- Evaluated: before every buy, immediately after every sell (a
  loss-realising sell latches+alerts right away, not on the next buy
  attempt), by `/claude_status`, and every 60s by the periodic monitor
  below — so an unrealised drawdown is caught even with the bot sitting
  idle, no trade required.
- On an owner-authorised restart, the OLD (higher, pre-drawdown) HWM is
  discarded — `reset_equity_baseline_after_restart()` sets a fresh HWM at
  current equity, not left as a ceiling that would make the very next tick
  look like a smaller drawdown than it is.

**Periodic health monitor** (`claude_monitor.py`, a 60s daemon thread
started the same way `learnerbot/telegram_ai_ops_patch.py`'s own watcher
is — wrapping `learnerbot.cli._app`, one hook, not a second one). Added
because the previous design only rejected the *next* entry attempt if a
critical precondition failed while sitting `ARMED` — review required an
*active* transition instead. Every tick: reconciles+re-evaluates drawdown
(can latch with no trade involved) and, if `ARMED`, calls the same
`armed_health_check()` `/claude_arm_live` uses; on any failure it calls
`claude_state.force_off()` — an active, system-triggered `→ OFF` — and
sends the owner a one-time alert.

`armed_health_check()` (strengthened per review, 2026-08-26) checks, in
order: risk config valid, signer ready, chain authorised, the **real**
operator kill switch (`app.operator_settings()['engine_enabled']` — the
exact key `learnerbot/cli.py`, `telegram_ui.py`, and `fast_market.py` all
read; an earlier version read `app.general()`, a different CSV that
doesn't carry this key at all, so the check was silently always-on
regardless of the actual switch — caught by review), Claude quarantine
intact (`learnerbot.config.load_dotenv is claude_bot_quarantine._noop_load_dotenv`),
the Claude state machine installed, the one authoritative Telegram router
installed (`learnerbot.telegram_ui.handle_update is telegram_control_patch.handle_update`),
both Solana execution guards still the effective wrapper on
`SolanaLiveExecutor.buy`/`.sell`, and all four EVM signing/broadcast entry
points `evm_execution_guard_patch.py` unconditionally guards still denied —
`LiveTrader.buy`/`.sell`/`.execute_cycle`/`.execute_v3_cycle` each checked
individually against their `_guarded_*` counterpart (an earlier version
checked only `.buy`, which review correctly flagged: sell/execute_cycle/
execute_v3_cycle could have been displaced while buy stayed intact and the
check would still report healthy). Buy/sell identity alone was reviewed as
insufficient proof the whole runtime composition was intact. Signer
fail-closed behaviour is proven by the signer/identity check itself, not a
second redundant check. `_guarded_buy()` itself calls `armed_health_check()`
as its first check (consolidated per review rather than duplicating a
partial copy of the same checks), so every one of these — including the
unpriced-position check above — also blocks new entries directly, not just
`/claude_arm_live`. This module has no code path to arm,
clear `HALTED_DRAWDOWN`, sign, or broadcast — structurally proven (not just
behaviorally) in `tests/test_claude_execution_and_telegram.py`.

Commands (all owner-only — sender's Telegram id must exactly match
`CLAUDE_BOT_WALLET_OWNER_ID`; a non-owner sender is flatly refused):
- `/claude_status` — read-only: operating state, drawdown latch, current
  drawdown %, high-water/current equity, open positions / 10, aggregate
  exposure % / 30% ceiling, per position % / 3% ceiling, signer readiness,
  authorised chain(s). No secrets.
- `/claude_arm_live CONFIRM` — `OFF → ARMED`. Refused (with the specific
  reason) by `armed_health_check()` if risk config is invalid, signer isn't
  ready, no chain is authorised, the kill-switch is active, or the guard is
  not composed; separately refused if `HALTED_DRAWDOWN` is active.
- `/claude_disarm` — immediate `→ OFF`, no confirmation required.
- `/claude_stop` — immediate `→ STOPPING → OFF`, no confirmation required.
  New entries blocked at once; open positions remain exitable.
- `/claude_restart_request` — valid only while `HALTED_DRAWDOWN`; issues a
  single-use challenge that expires after
  `claude_state.RESTART_CHALLENGE_TTL_SECONDS` (300s).
- `/claude_restart_confirm CONFIRM` — consumes the challenge (so a
  stale/replayed confirm is always rejected), rechecks the same
  `armed_health_check()` preconditions, clears `HALTED_DRAWDOWN`, and resets
  the equity high-water-mark to a fresh baseline. Operating state stays
  `OFF` afterward — resuming LIVE entries still requires a separate
  `/claude_arm_live CONFIRM`.

No command or monitor tick here can reach `SolanaLiveExecutor.buy`/`.sell`
directly, bypass `risk_engine_guard`, bypass `signing_interface`, bypass
`AUTHORISED_CHAINS`, or bypass the reused PoolCheck/RugCheck/slippage/
liquidity gates in `learnerbot` — this router and monitor only ever flip
`claude_state` flags those other, independent checks already consult on
every guarded buy/sell.

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
