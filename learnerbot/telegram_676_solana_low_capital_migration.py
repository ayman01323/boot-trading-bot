from __future__ import annotations

import os
import time
from pathlib import Path

from . import cli as _cli
# Install the general execution-validation correction and clear this account's
# historical false missing-swapEvents fault counter before applying low-capital limits.
from . import telegram_676_clear_false_swap_event_faults_migration  # noqa: F401
# Re-assert the wallet-bound/reconciliation-safe Solana exit path after all
# preceding Solana compatibility and diagnostics layers have loaded. This guard
# is platform-wide; importing it here is only a late-startup ordering point.
from . import solana_final_runtime_guard_patch  # noqa: F401
from .user_registry import set_user_setting

TARGET_TELEGRAM_ID = "6760898817"
MARKER = ".telegram_6760898817_solana_low_capital_20260828_v2"
LEGACY_REAPPLY_ENV = "ALLOW_LEGACY_676_SOLANA_LOW_CAPITAL_MIGRATION"
_PREV_APP = _cli._app


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _apply(app) -> None:
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return

    # Trade size is fixed at 0.009 SOL in live_limits; this per-user value is kept
    # only for display/audit consistency and no longer sizes trades.
    set_user_setting(
        app.csv_dir,
        TARGET_TELEGRAM_ID,
        "solana_live_trade_sol",
        "0.009",
        chain_id=-101,
        description="Per-user Solana LIVE trade size (superseded by fixed 0.009 in live_limits)",
    )
    set_user_setting(
        app.csv_dir,
        TARGET_TELEGRAM_ID,
        "solana_live_min_reserve_sol",
        "0.005",
        chain_id=-101,
        description="Per-user low-capital Solana untouched reserve",
    )

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "\n".join([
            f"applied_epoch={int(time.time())}",
            f"telegram_id={TARGET_TELEGRAM_ID}",
            "solana_live_trade_sol=0.009",
            "solana_live_min_reserve_sol=0.005",
            "minimum_wallet_funding_sol=0.014",
            "simulation_required=true",
            "economic_overhead_gate_unchanged=true",
            "landed_fault_circuit_breaker_unchanged=true",
        ]) + "\n",
        encoding="utf-8",
    )
    print(
        "[telegram-676-solana-low-capital] tid=6760898817 "
        "trade=0.009 reserve=0.005 minimum_funding=0.014"
    )


def _app_with_676_low_capital():
    app = _PREV_APP()
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return app
    if not _bool(os.getenv(LEGACY_REAPPLY_ENV, "false"), False):
        print(
            "[telegram-676-solana-low-capital] historical migration retired; "
            "marker_missing=true automatic_reapply=false settings_written=false"
        )
        return app
    try:
        _apply(app)
    except Exception as exc:
        print(f"[telegram-676-solana-low-capital] ERROR {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_676_low_capital
