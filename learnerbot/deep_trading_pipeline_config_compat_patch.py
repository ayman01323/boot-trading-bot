from __future__ import annotations

"""Configuration-compatibility corrections for the consolidated pipeline repair.

The deep repair is deliberately flexible, but explicit operator/test configuration
must remain authoritative.  This layer derives the adaptive Solana profile from the
existing configured floors/caps instead of replacing them, and leaves the legacy
retry scheduler's 60-second cadence intact because the separate 10-minute Alchemy
network-call circuit already prevents provider hammering.
"""

from decimal import Decimal

from . import deep_trading_pipeline_repair_patch as _deep
from . import sibot_alchemy_retry_queue_patch as _retry
from . import sibot_legacy_backlog_drainer_patch as _drainer
from . import solana_leader_edge_alignment_patch as _leader
from . import solana_sibot as _sol


def _profile(cfg: dict) -> dict[str, Decimal | int]:
    configured_closed = max(1, _sol._int(cfg.get("min_closed_trades"), 10))
    mature_target = max(1, _sol._int(cfg.get("flex_mature_min_closed_trades"), 10))
    mature_min_closed = min(configured_closed, mature_target)
    early_target = max(1, _sol._int(cfg.get("flex_early_min_closed_trades"), 5))
    early_min_closed = min(mature_min_closed, early_target)

    configured_win = _sol._dec(cfg.get("min_win_rate_pct"), 65)
    configured_pf = _sol._dec(cfg.get("min_profit_factor"), "1.75")
    configured_dd = _sol._dec(cfg.get("max_leader_drawdown_pct"), 20)
    configured_recent_win = _sol._dec(cfg.get("min_recent_win_rate_pct"), 65)
    configured_recent_pf = _sol._dec(cfg.get("min_recent_profit_factor"), "1.50")

    mature_win = min(configured_win, _sol._dec(cfg.get("flex_mature_min_win_rate_pct"), 58))
    mature_pf = min(configured_pf, _sol._dec(cfg.get("flex_mature_min_profit_factor"), "1.40"))
    mature_dd = max(configured_dd, _sol._dec(cfg.get("flex_mature_max_drawdown_pct"), 25))
    mature_recent_win = min(
        configured_recent_win,
        _sol._dec(cfg.get("flex_mature_min_recent_win_rate_pct"), 55),
    )
    mature_recent_pf = min(
        configured_recent_pf,
        _sol._dec(cfg.get("flex_mature_min_recent_profit_factor"), "1.25"),
    )

    return {
        "early_min_closed": early_min_closed,
        "mature_min_closed": mature_min_closed,
        "mature_win": mature_win,
        "mature_pf": mature_pf,
        "mature_dd": mature_dd,
        "mature_recent_win": mature_recent_win,
        "mature_recent_pf": mature_recent_pf,
        # Early evidence must be stronger than the mature tier, unless an explicit
        # configuration already makes the mature threshold stricter.
        "early_win": max(mature_win, _sol._dec(cfg.get("flex_early_min_win_rate_pct"), 70)),
        "early_pf": max(mature_pf, _sol._dec(cfg.get("flex_early_min_profit_factor"), "1.80")),
        "early_dd": min(mature_dd, _sol._dec(cfg.get("flex_early_max_drawdown_pct"), 15)),
        "early_recent_win": max(
            mature_recent_win,
            _sol._dec(cfg.get("flex_early_min_recent_win_rate_pct"), 65),
        ),
        "early_recent_pf": max(
            mature_recent_pf,
            _sol._dec(cfg.get("flex_early_min_recent_profit_factor"), "1.50"),
        ),
    }


def _adaptive_pre_quality_ok(metrics: dict, cfg: dict) -> bool:
    p = _profile(cfg)
    if _sol._bool(cfg.get("require_complete_history"), True) and not metrics.get("history_complete"):
        return False
    if _sol._dec(metrics.get("net"), 0) <= 0:
        return False

    closed = int(metrics.get("closed") or 0)
    win = _sol._dec(metrics.get("win_rate"), 0)
    pf = _sol._dec(metrics.get("profit_factor"), 0)
    dd = _sol._dec(metrics.get("drawdown_pct"), 0)
    recent_win = _sol._dec(metrics.get("recent_win_rate"), 0)
    recent_pf = _sol._dec(metrics.get("recent_profit_factor"), 0)

    if closed >= int(p["mature_min_closed"]):
        return bool(
            win >= p["mature_win"]
            and pf >= p["mature_pf"]
            and dd <= p["mature_dd"]
            and recent_win >= p["mature_recent_win"]
            and recent_pf >= p["mature_recent_pf"]
        )
    if closed >= int(p["early_min_closed"]):
        return bool(
            win >= p["early_win"]
            and pf >= p["early_pf"]
            and dd <= p["early_dd"]
            and recent_win >= p["early_recent_win"]
            and recent_pf >= p["early_recent_pf"]
        )
    return False


def _adaptive_quality_failure_reason(metrics: dict, cfg: dict) -> str:
    p = _profile(cfg)
    if _sol._bool(cfg.get("require_complete_history"), True) and not metrics.get("history_complete"):
        return "history incomplete"
    if _sol._dec(metrics.get("net"), 0) <= 0:
        return "historical net profit is not positive"

    closed = int(metrics.get("closed") or 0)
    if closed < int(p["early_min_closed"]):
        return "not enough closed trades"

    early = closed < int(p["mature_min_closed"])
    prefix = "early-sample " if early else ""
    win_floor = p["early_win"] if early else p["mature_win"]
    pf_floor = p["early_pf"] if early else p["mature_pf"]
    dd_cap = p["early_dd"] if early else p["mature_dd"]
    recent_win_floor = p["early_recent_win"] if early else p["mature_recent_win"]
    recent_pf_floor = p["early_recent_pf"] if early else p["mature_recent_pf"]

    if _sol._dec(metrics.get("win_rate"), 0) < win_floor:
        return prefix + "historical win rate below adaptive minimum"
    if _sol._dec(metrics.get("profit_factor"), 0) < pf_floor:
        return prefix + "historical profit factor below adaptive minimum"
    if _sol._dec(metrics.get("drawdown_pct"), 0) > dd_cap:
        return prefix + "historical drawdown above adaptive maximum"
    if _sol._dec(metrics.get("recent_win_rate"), 0) < recent_win_floor:
        return prefix + "recent win rate below adaptive minimum"
    if _sol._dec(metrics.get("recent_profit_factor"), 0) < recent_pf_floor:
        return prefix + "recent profit factor below adaptive minimum"

    historical_floor = max(Decimal(0), _sol._dec(cfg.get("live_min_leader_median_return_pct"), "5"))
    recent_floor = max(Decimal(0), _sol._dec(cfg.get("live_min_leader_recent_median_return_pct"), "4"))
    if _sol._dec(metrics.get("median_return_pct"), 0) < historical_floor:
        return "median return below LIVE edge floor"
    if _sol._dec(metrics.get("recent_median_return_pct"), 0) < recent_floor:
        return "recent median return below LIVE edge floor"
    return "quality gate failed"


def install() -> None:
    # The 10-minute deep-provider circuit blocks actual Alchemy calls during
    # pressure. Keep legacy scheduling defaults untouched so existing operational
    # semantics/tests and explicit CSV settings continue to work.
    _retry._TRANSIENT_RETRY_COOLDOWN_SECONDS = 60
    _drainer._DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60

    _deep._profile = _profile
    _deep._adaptive_pre_quality_ok = _adaptive_pre_quality_ok
    _deep._adaptive_quality_failure_reason = _adaptive_quality_failure_reason
    _leader._PREV_HISTORICAL_OK = _adaptive_pre_quality_ok
    _leader._quality_failure_reason = _adaptive_quality_failure_reason
    print(
        "[deep-trading-config-compat] configured_thresholds_authoritative=true "
        "alchemy_network_circuit=600s legacy_scheduler_defaults=preserved",
        flush=True,
    )


install()
