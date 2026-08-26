"""Quarantines historical production migrations before learnerbot's patch
chain ever imports, and blocks any fallback to the shared production .env.

Background: learnerbot/__main__.py unconditionally imports ~60 patch
modules, several of which are one-time PRODUCTION migrations gated by a
marker file. A real full-chain test run (see verify_bootstrap_composition.py)
proved these replay against ANY fresh instance lacking their marker --
including this isolated Claude instance -- and do real, unwanted things:
create production user rows, and (per direct code inspection of
polygon_live_enable_migration.py) set auto_trading_enabled=true,
live_trading_enabled=true, recommendation_mode=ARMED, sibot_auto_trade_enabled=true
if Polygon connectivity is ever configured. This is exactly the kind of
automatic arming this bot must never do.

A full audit (grep across every learnerbot/*.py for the marker-file gating
pattern, cross-referenced against learnerbot/__main__.py's import list,
including transitive imports) found 12 such migrations, split by where their
marker lives:

  ISOLATED marker (Path(app.data_dir)/...) -- the marker itself doesn't leak
  into production, but the migration's PAYLOAD writes app.csv_dir, i.e. THIS
  instance's own isolated CSV config, which is still unwanted (user
  creation, auto-arming) even though it doesn't touch real production data:
    - telegram_account_roles_patch.py
    - telegram_676_solana_low_capital_migration.py
    - telegram_676_full_live_migration.py (also gated by
      ALLOW_LEGACY_676_FULL_LIVE_MIGRATION, default false -- quarantined
      here anyway, defense in depth)
    - telegram_676_clear_false_swap_event_faults_migration.py
    - polygon_live_enable_migration.py (two markers: MARKER, VERIFIED_MARKER)

  SHARED marker (Path(__file__).resolve().parent.parent / "data" / ...) --
  hardcoded to the physical location of the learnerbot package on disk,
  completely ignoring CSV_DIR/DATA_DIR. On the Google-managed checkout this
  is shared infrastructure this bot has no business mutating regardless of
  how "live" any data there actually is:
    - solana_minimum_settings_migration.py
    - solana_quality_settings_migration.py
    - solana_frequency_settings_migration.py
    - solana_live_latency_settings_migration.py
    - solana_position_capacity_migration.py
    - solana_emergency_loss_halt_migration.py
    - solana_operator_writeoff_8fip_migration.py

Deliberately NOT quarantined: sibot_profit_guard_patch.py's two
`.sibot_quality_guard_v1` / `.solana_quality_guard_v1` markers. Those
migrations only tighten quality-gate thresholds in this instance's own
isolated CSV -- they don't create users, don't arm anything, and don't touch
a repo-root path. Quarantining a safety-tightening migration would be the
wrong kind of caution.

This list was built from an actual audit of the current codebase, not
assumed exhaustive for all time -- if learnerbot gains a new migration with
this same pattern later, it needs to be added here. verify_bootstrap_composition.py
actively checks (not just trusts) that none of the specific effects listed
above occurred, so a gap here is a test failure, not a silent gap.
"""

from __future__ import annotations

import os
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

# (relative-to-data_dir marker name,) -- Path(app.data_dir) / name
ISOLATED_MARKERS = (
    ".telegram_roles_20260818_v1",
    ".telegram_6760898817_solana_low_capital_20260818_v1",
    ".telegram_6760898817_full_live_20260818_v1",
    ".telegram_6760898817_clear_false_swap_event_faults_20260818_v1",
    ".polygon_live_enabled_20260821_v1",
    ".polygon_live_verified_20260821_v1",
)

# Path(__file__).resolve().parent.parent / "data" / name, i.e. REPO_ROOT/data/name
SHARED_MARKERS = (
    ".solana_minimum_settings_20260817_applied",
    ".solana_quality_settings_20260818_applied",
    ".solana_balanced_frequency_20260818_applied",
    ".solana_live_latency_priority_20260818_v1",
    ".solana_live_position_capacity_2_20260818_applied",
    ".solana_emergency_loss_halt_20260818_v1",
    ".solana_operator_writeoff_8fip_20260823_applied",
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


def quarantine_historical_migrations(app) -> list[str]:
    """Pre-create every known migration marker so none of them mutate
    anything on import. Returns the list of markers actually created (empty
    on a second call / already-quarantined instance -- idempotent)."""
    created: list[str] = []

    data_dir = Path(app.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ISOLATED_MARKERS:
        marker = data_dir / name
        if not marker.exists():
            marker.write_text("quarantined-by-claude-trading-bot\n", encoding="utf-8")
            created.append(str(marker))

    shared_data_dir = REPO_ROOT / "data"
    shared_data_dir.mkdir(parents=True, exist_ok=True)
    for name in SHARED_MARKERS:
        marker = shared_data_dir / name
        if not marker.exists():
            marker.write_text("quarantined-by-claude-trading-bot\n", encoding="utf-8")
            created.append(str(marker))

    return created


def block_production_env_fallback() -> list[str]:
    """Blank every known secret-shaped env var this bot doesn't need, but
    only if this instance's own env file didn't already set it -- never
    overwrites a value this instance's own .env explicitly provided.
    Returns the list of names actually blanked."""
    blanked: list[str] = []
    for name in _PRODUCTION_ONLY_SECRETS:
        if name not in os.environ:
            os.environ[name] = ""
            blanked.append(name)
    return blanked
