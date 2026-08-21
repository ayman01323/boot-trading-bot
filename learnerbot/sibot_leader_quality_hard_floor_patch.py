from __future__ import annotations

from decimal import Decimal

from . import sibot as _sibot

# Per-user sibot_<key> overrides (set via Telegram commands like /sibotminwin, or
# left over from an old preset) can silently weaken the platform-restored EVM
# leader-quality bar, because _leader_quality_ok() and the copied-performance check
# in _validate_entry() (both in sibot_profit_guard_patch.py) read these thresholds
# straight from cfg with no floor protecting them -- unlike the equivalent Solana
# gate (solana_leader_quality_restore_patch.py), which already enforces hard floors
# via max()/min() against configured values. Confirmed in practice: one account was
# running min_win_rate_pct=45 against a platform default of 55, directly weakening
# which leaders get selected for that account with nothing else catching it.
#
# These floors/ceilings mirror the platform defaults this codebase's own
# sibot_profit_guard_patch.py._migrate_platform_once already enforces platform-wide;
# this just makes sure no per-user override -- existing or future -- can go past
# them, the same way the Solana side already cannot.
_QUALITY_FLOORS = {
    "min_closed_trades": "50",
    "min_win_rate_pct": "55",
    "min_profit_factor": "1.5",
    "min_recent_win_rate_pct": "55",
    "min_recent_profit_factor": "1.10",
    "min_copied_win_rate_pct": "50",
    "min_copied_profit_factor": "1.50",
    "leader_suspend_minutes": "1440",
}
_QUALITY_CEILINGS = {
    "max_leader_drawdown_pct": "20",
    "max_consecutive_copied_losses": "2",
    "min_copied_trades_for_guard": "3",
}

_PREV_USER_SETTINGS = _sibot.user_settings


def _d(v, default="0") -> Decimal:
    return _sibot._dec(v, default)


def user_settings_with_quality_floor(app, telegram_id, chain_id=0) -> dict:
    cfg = dict(_PREV_USER_SETTINGS(app, telegram_id, chain_id))
    cfg["require_complete_history"] = "true"
    for key, floor in _QUALITY_FLOORS.items():
        cfg[key] = str(max(_d(cfg.get(key), floor), _d(floor)))
    for key, ceiling in _QUALITY_CEILINGS.items():
        cfg[key] = str(min(_d(cfg.get(key), ceiling), _d(ceiling)))
    return cfg


def install():
    if getattr(_sibot, "_leader_quality_hard_floor_installed", False):
        return
    _sibot.user_settings = user_settings_with_quality_floor
    _sibot._leader_quality_hard_floor_installed = True
    print(
        "[sibot-leader-quality-floor] history_complete=true win_rate>=55% pf>=1.5 "
        "recent_win_rate>=55% recent_pf>=1.10 drawdown<=20% copied_win_rate>=50% "
        "copied_pf>=1.50 consecutive_loss_limit<=2 min_copied_trades_for_guard<=3 "
        "leader_suspend>=1440m no_per_user_override_past_platform_floor=true"
    )


install()
