"""Quarantines historical production migrations and blocks production-.env
fallback, BEFORE any `learnerbot` module is ever imported, in both the
parent process (run.py) and the child (bootstrap_run.py).

Background: learnerbot/__main__.py unconditionally imports ~60 patch
modules, several of which are one-time PRODUCTION migrations. A real
full-chain test run proved these replay against ANY fresh instance lacking
their marker file -- including this isolated Claude instance -- and do real,
unwanted things: create production user rows, and (per direct code
inspection of polygon_live_enable_migration.py) set
auto_trading_enabled=true, live_trading_enabled=true,
recommendation_mode=ARMED, sibot_auto_trade_enabled=true if Polygon
connectivity is ever configured.

First version of this module pre-created these migrations' own marker files
to make them see themselves as "already applied". Review correctly rejected
that: it still wrote files under REPO_ROOT/data, and the target invariant is
ZERO repo-root writes, not "writes limited to inert marker files". This
version does not write anything to prevent them -- it replaces each risky
migration module with an empty (or minimally-shimmed, see WRITEOFF_SHIM
below) stand-in BEFORE learnerbot ever imports it, via sys.modules
pre-population. Python's import system checks sys.modules first; if a name
is already there, the real file's code never executes at all. Verified safe
for all 12: every one of them is either a bare `noqa: F401` side-effect-only
import (all 12 confirmed via full-repo grep -- nothing anywhere accesses an
attribute on 11 of them) or, for the one exception
(solana_operator_writeoff_8fip_migration, called via `.apply()` by
final_runtime_integrity_patch.py unconditionally and by
solana_live_position_scope_fix_patch.py inside its own try/except), given a
harmless no-op `apply()` so those callers don't break.

Full audit list (grep across every learnerbot/*.py for the marker-file
gating pattern, cross-referenced against learnerbot/__main__.py's import
list including transitive imports) -- 12 migrations:
    telegram_account_roles_patch
    telegram_676_solana_low_capital_migration
    telegram_676_full_live_migration
    telegram_676_clear_false_swap_event_faults_migration
    polygon_live_enable_migration
    solana_minimum_settings_migration
    solana_quality_settings_migration
    solana_frequency_settings_migration
    solana_live_latency_settings_migration
    solana_position_capacity_migration
    solana_emergency_loss_halt_migration
    solana_operator_writeoff_8fip_migration

Deliberately NOT stubbed: sibot_profit_guard_patch.py's two
`.sibot_quality_guard_v1` / `.solana_quality_guard_v1` markers. Those
migrations only tighten quality-gate thresholds in this instance's own
isolated CSV -- they don't create users, don't arm anything, and don't touch
a repo-root path. Stubbing a safety-tightening migration would be the wrong
kind of caution, and it was never a repo-root-write risk in the first place.

This list was built from an actual audit of the current codebase, not
assumed exhaustive for all time -- if learnerbot gains a new migration with
this same pattern later, it needs to be added here.
verify_bootstrap_composition.py actively checks (not just trusts) that zero
repo-root files changed, so a gap here is a test failure, not a silent gap.
"""

from __future__ import annotations

import os
import sys
import types

STUBBED_MIGRATIONS = (
    "telegram_account_roles_patch",
    "telegram_676_solana_low_capital_migration",
    "telegram_676_full_live_migration",
    "telegram_676_clear_false_swap_event_faults_migration",
    "polygon_live_enable_migration",
    "solana_minimum_settings_migration",
    "solana_quality_settings_migration",
    "solana_frequency_settings_migration",
    "solana_live_latency_settings_migration",
    "solana_position_capacity_migration",
    "solana_emergency_loss_halt_migration",
    "solana_operator_writeoff_8fip_migration",
)

# Sensitive env var names learnerbot reads that this bot never needs and must
# never inherit from the shared repo's production .env. Built from an audit
# of every os.getenv/os.environ.get call across the package for anything
# secret/token/key-shaped. learnerbot/config.py's load_dotenv(BOT_ROOT/.env)
# has no override=True, so any of these NOT already present in os.environ by
# the time that call happens gets silently filled from production's real
# .env -- blanking them first (only if this instance's own env didn't
# already set them) closes that gap, since python-dotenv only fills a key
# that is not already present in os.environ at all, blank or not.
#
# This is an audited, enumerated list, not a mathematical guarantee against
# every conceivable current or future os.getenv call anywhere in learnerbot
# -- if that package adds a new secret-shaped var later without a
# corresponding entry here, it could fall through until the audit is redone.
_PRODUCTION_ONLY_SECRETS = (
    "LIVE_WALLET_PRIVATE_KEY",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "HELIUS_RPC_URL",
    "HELIUS_WS_URL",
    "HELIUS_API_KEY",
    "SOLANA_RPC_URLS",
    "SOLANA_RPC_FALLBACK_URLS",
    "SOLANA_EXPLORER_URL",
)

_ALREADY_STUBBED = False


def stub_out_historical_migrations() -> list[str]:
    """Insert an empty (or minimally-shimmed) stand-in module into
    sys.modules for every name in STUBBED_MIGRATIONS, so the real file's
    code never executes when something later does
    `from . import <name>`. MUST be called before `import learnerbot`
    (any submodule) for the first time in this process -- if the real
    module is already in sys.modules by the time this runs, it's too late,
    the mutation already happened. Idempotent; returns the names actually
    stubbed (empty on a second call in the same process).
    """
    global _ALREADY_STUBBED
    if _ALREADY_STUBBED:
        return []
    stubbed: list[str] = []
    for name in STUBBED_MIGRATIONS:
        full_name = f"learnerbot.{name}"
        if full_name in sys.modules:
            # Too late to stub -- the real module already ran. This is a
            # logic error in call ordering, not something to silently
            # ignore.
            raise RuntimeError(
                f"{full_name} is already imported -- stub_out_historical_migrations() "
                f"must run before any learnerbot import, not after"
            )
        stub = types.ModuleType(full_name)
        if name == "solana_operator_writeoff_8fip_migration":
            # final_runtime_integrity_patch.py calls .apply() on this one
            # unconditionally (no try/except); give it a harmless no-op with
            # the real function's signature so that call doesn't break.
            stub.apply = lambda root=None: False
        sys.modules[full_name] = stub
        stubbed.append(full_name)
    _ALREADY_STUBBED = True
    return stubbed


def block_production_env_fallback() -> list[str]:
    """Blank every known secret-shaped env var this bot doesn't need, but
    only if this instance's own env file didn't already set it -- never
    overwrites a value this instance's own .env explicitly provided.
    Returns the list of names actually blanked. Safe to call multiple times
    (idempotent) and safe to call before any learnerbot import (no
    dependency on it)."""
    blanked: list[str] = []
    for name in _PRODUCTION_ONLY_SECRETS:
        if name not in os.environ:
            os.environ[name] = ""
            blanked.append(name)
    return blanked


def quarantine_before_any_learnerbot_import() -> None:
    """Single entry point: call this as the very first thing, before any
    `import learnerbot` (including `from learnerbot.config import
    AppSettings`), in every process that will touch learnerbot -- both
    run.py's parent process and bootstrap_run.py's child (sys.modules does
    not survive os.execvpe(), so the child must call this fresh too)."""
    if "learnerbot" in sys.modules or any(m.startswith("learnerbot.") for m in sys.modules):
        raise RuntimeError(
            "learnerbot is already imported -- quarantine_before_any_learnerbot_import() "
            "must run before the first learnerbot import in this process, not after"
        )
    stub_out_historical_migrations()
    block_production_env_fallback()
