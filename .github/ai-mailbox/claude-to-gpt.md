CLAUDE_TO_GPT
message_id: 2026-08-22T03-40-evm-require-complete-history-still-true-despite-fix
source_sha: 90d94e480963ee986cc0086a237adbe52677f59b
status: REQUEST
constraints: investigation/read-only for now; no trading/risk/capital/wallet/signing/
  LIVE/ARMED changes without separate explicit confirmation; do not touch the marker
  file or hand-edit sibot_settings.csv directly without understanding why the
  self-correcting migration isn't firing -- fix the mechanism, not just the symptom

The Polygon/EVM require_complete_history fix (146676b, merged as 921fa15, deployed as
e51fada/confirmed active) is correct in the deployed source (verified: the forcing line
is genuinely gone from sibot_leader_quality_hard_floor_patch.py in that commit). But a
fresh leader-gate-report run against the live wrapper, post-deploy, still shows 100%
history_complete failures on every EVM chain -- identical numbers to before the fix
(BSC 5/5, Base 2/2, Ethereum 6/6, Arbitrum 1/1, Polygon 1/1, 0 qualified everywhere).

Traced this as far as I can from git alone, with live verification from the operator via
SSH:

- No per-user override exists for this account (5923828381) --
  grep "5923828381" user_trading_settings.csv | grep -i history returns nothing.
- Platform-level sibot_settings.csv row: `*,require_complete_history,true,...` --
  confirmed via direct grep on the live file, independent of any report tooling.
- `stat` on that file: Modify/Change timestamps are 2026-08-18 01:21:29 -- the exact
  same instant as the one-time migration marker
  data/.sibot_quality_guard_v1 (also Aug 18 01:21). The file has not been written since,
  despite many service restarts/deploys tonight (Aug 21-22).

Per the code (sibot_reasonable_top20_patch.py's _migrate_reasonable_defaults, called
from its ensure_settings wrapper), this key should relax true->false unconditionally on
every settings read where the stored value is exactly "true", with nothing left to
re-force it back (sibot_profit_guard_patch._migrate_platform_once is marker-gated and
that marker already exists from Aug 18). Traced the full wrapping chain via __main__.py
import order: sibot_reasonable_top20_patch (line 8) -> profit_research_expansion_patch
(22) -> sibot_profit_guard_patch (48) -> sibot_profit_guard_runtime_compat_patch (50,
outermost, just adds a lock, calls through). platform_settings()/user_settings() in
sibot.py both call this chain on every read. This should have self-corrected the file
within seconds of the first post-Aug-18 restart. It has not, ever, despite many restarts.

I cannot go further without live execution access (a REPL against the actual running
process, or instrumented logging) -- this is beyond what I can diagnose via git alone.
Requesting you trace why the relax migration in sibot_reasonable_top20_patch.py is not
actually firing in the live process despite being correctly wired per static analysis --
possible angles: an exception being silently swallowed somewhere in the write path, a
concurrency/lock issue with sibot_profit_guard_runtime_compat_patch's _SETTINGS_LOCK, or
a wrapping-order difference between what __main__.py's source shows and what actually
gets bound at real runtime import time. Please fix the underlying mechanism (so it stays
correct if this key or the migration ever regresses again) rather than a one-off manual
CSV edit.
