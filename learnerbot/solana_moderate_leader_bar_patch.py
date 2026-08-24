from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import solana_sibot as _sol


# Owner-requested moderate Solana leader qualification profile.
# This patch changes only leader research/selection thresholds. It does not alter
# liquidity checks, price-impact/slippage ceilings, reserve requirements,
# simulation, signing, platform realised-PF recovery controls, mint quarantine,
# stuck-position safety, stop-loss/exit controls, or transaction construction.
_PROFILE = {
    "min_closed_trades": ("5", "min"),
    "min_win_rate_pct": ("50", "min"),
    "min_profit_factor": ("1.35", "min"),
    "max_leader_drawdown_pct": ("30", "max"),
    "min_recent_win_rate_pct": ("55", "min"),
    "min_recent_profit_factor": ("1.20", "min"),
    "live_min_leader_median_return_pct": ("2.5", "min"),
    "live_min_leader_recent_median_return_pct": ("2.0", "min"),
}

_PREV_SETTINGS = _sol.settings


def _dec(value, fallback: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(fallback)


def apply_profile(cfg: dict) -> dict:
    """Return a copy with only stricter-than-profile leader bars relaxed.

    Existing operator settings that are already looser are preserved. Complete
    history remains fail-closed by default and positive historical net profit is
    still mandatory in the existing leader gate.
    """
    out = dict(cfg or {})
    out["require_complete_history"] = "true"
    for key, (target_text, direction) in _PROFILE.items():
        target = Decimal(target_text)
        current = _dec(out.get(key), target_text)
        if direction == "min":
            if current > target:
                out[key] = target_text
            elif key not in out:
                out[key] = target_text
        else:
            if current < target:
                out[key] = target_text
            elif key not in out:
                out[key] = target_text
    return out


def settings_with_moderate_leader_bar(app) -> dict:
    return apply_profile(_PREV_SETTINGS(app))


def install() -> None:
    if getattr(_sol, "_moderate_leader_bar_installed", False):
        return
    _sol.settings = settings_with_moderate_leader_bar
    _sol._moderate_leader_bar_installed = True
    print(
        "[solana-leader-bar] profile=moderate "
        "min_closed=5 min_win=50 min_pf=1.35 max_dd=30 "
        "recent_win=55 recent_pf=1.20 median=2.5 recent_median=2.0 "
        "complete_history=true positive_net_required=true execution_safety_unchanged=true"
    )


install()
