from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal

from . import profit_control_loop_patch as _profit_control
from . import solana_live_patch as _live
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

# Restore the Solana trading-policy behaviour that was active around the first
# profitable confirmed-fast-lane period, while retaining all later execution,
# accounting, liquidity, simulation, reserve, deduplication and circuit-breaker
# protections.  The reference commit is the confirmed-fast-lane strategy state
# immediately before the later profit-first / immediate-leader-exit strategy
# rewrites were introduced.
FIRST_DAY_REFERENCE_COMMIT = "f0ca88450fe96a316dc15e676fab1e36c1137285"

# These are the effective balanced-frequency strategy values present in that
# reference state.  They intentionally control selection/timing/exit policy only.
# They do NOT change LIVE arming, trade size, reserve, signing, simulation,
# execution fee/impact caps, wallet binding, transaction validation or circuit
# breakers added later.
FIRST_DAY_STRATEGY_TARGETS = {
    "leaders_per_user": "5",
    "min_closed_trades": "5",
    "min_win_rate_pct": "50",
    "require_complete_history": "false",
    "min_profit_factor": "1.20",
    "recent_trade_window": "10",
    "min_recent_win_rate_pct": "50",
    "min_recent_profit_factor": "1.00",
    "max_leader_drawdown_pct": "30",
    "min_copied_trades_for_guard": "5",
    "min_copied_win_rate_pct": "40",
    "min_copied_profit_factor": "1.0",
    "max_consecutive_copied_losses": "3",
    "leader_suspend_minutes": "180",
    "max_signal_age_seconds": "30",
    "max_roundtrip_loss_pct": "3",
    "max_entry_deterioration_pct": "2",
    "discovery_blocks_per_cycle": "4",
    "discovery_interval_seconds": "10",
    "candidate_limit": "150",
    "history_max_signatures": "400",
    "history_refresh_hours": "8",
    "leader_poll_seconds": "4",
    "position_poll_seconds": "10",
    "stop_loss_pct": "10",
    "take_profit_pct": "25",
    "leader_exit_loss_cap_pct": "2",
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "0.25",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "4",
    "max_hold_hours": "24",
    "mirror_partial_sells": "true",

    # Protective follower-economics overlay requested after the restored
    # first-day leader strategy.  It can only reject a BUY; it cannot create a
    # trade or weaken any existing execution/liquidity/simulation protection.
    "live_require_positive_executable_edge": "true",
    "live_executable_edge_min_samples": "5",
    "live_executable_edge_haircut_pct": "35",
    "live_executable_edge_latency_reserve_pct": "0.25",
    "live_min_executable_net_edge_pct": "0.25",
}

# profit_control_loop captured the normal Solana settings function before the
# later strategy wrappers were installed.  Use it as the base so current operator
# LIVE/risk/execution settings still load, then overlay only the first-day strategy
# fields above.
_BASE_SETTINGS = _profit_control._PREV_SETTINGS

# At this import point the audited preflight-cache layer is already installed.
# Preserve it as the inner validator so signal freshness, Jupiter BUY->SELL quote
# round-trip loss, entry deterioration and quote caching all remain authoritative.
_PREV_VALIDATE = _sol._validate_shadow_entry


def settings_first_day_strategy(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg.update(FIRST_DAY_STRATEGY_TARGETS)
    cfg["solana_strategy_profile"] = "FIRST_DAY_CONFIRMED_FAST_LANE_POSITIVE_EXECUTABLE_EDGE"
    cfg["solana_strategy_reference_commit"] = FIRST_DAY_REFERENCE_COMMIT
    return cfg


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    return sum(values, Decimal(0)) / Decimal(len(values))


def leader_expected_move_pct(app, wallet: str, cfg: dict) -> dict:
    """Estimate a deliberately conservative follower gross-move budget.

    The first-day strategy still discovers leaders from realised leader history.
    This helper does not treat that history as executable follower profit.  It
    merely supplies a gross expected-move ceiling which is then haircutted and
    charged for the follower's *current* Jupiter round-trip, network fees,
    slippage reserve and latency reserve before a BUY is allowed.
    """
    lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    recent_n = max(3, min(50, _sol._int(cfg.get("recent_trade_window"), 10)))
    min_samples = max(3, min(50, _sol._int(cfg.get("live_executable_edge_min_samples"), 5)))

    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            "SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
            (str(wallet), cutoff),
        ).fetchall()

    returns: list[Decimal] = []
    for row in rows:
        cost = _sol._dec(row["cost_sol"], 0)
        net = _sol._dec(row["net_sol"], 0)
        if cost <= 0:
            continue
        pct = net * Decimal(100) / cost
        # Prevent a single reconstructed outlier from creating artificial edge.
        returns.append(max(Decimal("-50"), min(Decimal("100"), pct)))

    recent = returns[-recent_n:]
    historical_mean = _mean(returns)
    recent_mean = _mean(recent)
    expected = max(Decimal(0), min(historical_mean, recent_mean)) if returns and recent else Decimal(0)
    return {
        "closed": len(returns),
        "recent_closed": len(recent),
        "minimum_samples": min_samples,
        "historical_mean_return_pct": historical_mean,
        "recent_mean_return_pct": recent_mean,
        "expected_move_pct": expected,
    }


def _follower_edge_components(app, event: dict, allocation_sol: Decimal, cfg: dict, check: dict) -> dict:
    metrics = leader_expected_move_pct(app, str(event.get("leader_wallet") or ""), cfg)
    expected = _sol._dec(metrics.get("expected_move_pct"), 0)

    haircut = max(Decimal(0), min(Decimal("90"), _sol._dec(cfg.get("live_executable_edge_haircut_pct"), 35)))
    conservative_expected = expected * (Decimal(100) - haircut) / Decimal(100)

    roundtrip = max(Decimal(0), _sol._dec((check or {}).get("roundtrip_loss_pct"), 100))
    allocation = Decimal(str(allocation_sol))
    entry_fee = max(Decimal(0), _sol._dec(cfg.get("estimated_entry_fee_sol"), "0.00002"))
    exit_fee = max(Decimal(0), _sol._dec(cfg.get("estimated_exit_fee_sol"), "0.00002"))
    fee_pct = ((entry_fee + exit_fee) * Decimal(100) / allocation) if allocation > 0 else Decimal(100)

    # Reserve the configured LIVE slippage tolerance on both legs.  One basis
    # point is 0.01 percentage points.  This is intentionally conservative and
    # remains separate from the current quoted round-trip deterioration.
    slippage_bps = max(Decimal(0), _sol._dec(cfg.get("live_order_slippage_bps"), 50))
    slippage_reserve_pct = slippage_bps * Decimal(2) / Decimal(100)
    latency_reserve_pct = max(
        Decimal(0),
        _sol._dec(cfg.get("live_executable_edge_latency_reserve_pct"), "0.25"),
    )

    net_edge = (
        conservative_expected
        - roundtrip
        - fee_pct
        - slippage_reserve_pct
        - latency_reserve_pct
    )
    return {
        **metrics,
        "haircut_pct": haircut,
        "conservative_expected_move_pct": conservative_expected,
        "current_roundtrip_loss_pct": roundtrip,
        "estimated_network_fee_pct": fee_pct,
        "slippage_reserve_pct": slippage_reserve_pct,
        "latency_reserve_pct": latency_reserve_pct,
        "executable_net_edge_pct": net_edge,
    }


def validate_entry_positive_executable_edge(app, event: dict, allocation_sol: Decimal, cfg: dict):
    """Require positive cost-adjusted follower edge before a Solana BUY.

    The existing Jupiter round-trip and deterioration preflight runs first and is
    never weakened.  The extra gate then asks whether a heavily haircutted leader
    move still exceeds *current follower* round-trip friction plus conservative
    network-fee, slippage and latency reserves.  Missing evidence fails closed.
    """
    ok, reason, check = _PREV_VALIDATE(app, event, allocation_sol, cfg)
    if not ok:
        return ok, reason, check

    if not _sol._bool(cfg.get("live_require_positive_executable_edge"), True):
        # Kept for deterministic tests/backward compatibility only.  The restored
        # production profile forces this key to true.
        return ok, reason, check

    edge = _follower_edge_components(app, event, Decimal(str(allocation_sol)), cfg, check or {})
    minimum_samples = int(edge.get("minimum_samples") or 5)
    if int(edge.get("closed") or 0) < minimum_samples or int(edge.get("recent_closed") or 0) < minimum_samples:
        detail = {
            **(check or {}),
            **edge,
        }
        return False, (
            "positive executable edge unavailable: leader has "
            f"{int(edge.get('closed') or 0)} historical / {int(edge.get('recent_closed') or 0)} recent "
            f"samples; need {minimum_samples}"
        ), detail

    minimum_net = max(
        Decimal(0),
        _sol._dec(cfg.get("live_min_executable_net_edge_pct"), "0.25"),
    )
    net_edge = _sol._dec(edge.get("executable_net_edge_pct"), -100)
    detail = {**(check or {}), **edge, "minimum_executable_net_edge_pct": minimum_net}
    if net_edge < minimum_net:
        return False, (
            f"positive executable edge rejected: follower net edge {net_edge:.3f}% < {minimum_net:.3f}% "
            f"after current round-trip {edge['current_roundtrip_loss_pct']:.3f}%, "
            f"fees {edge['estimated_network_fee_pct']:.3f}%, slippage reserve {edge['slippage_reserve_pct']:.3f}% "
            f"and latency reserve {edge['latency_reserve_pct']:.3f}%"
        ), detail

    return True, "PASS_POSITIVE_EXECUTABLE_EDGE", detail


def copied_ok_first_day(app, tid, wallet, cfg):
    """Use the first-day copied-leader evidence thresholds.

    Keep the corrected current P&L/accounting implementation underneath, but do
    not impose the later 2-trade hard PF=1.50 gate, first-loss 24h quarantine or
    platform/mint amount gates.  This reproduces the earlier strategy threshold
    logic: after the configured sample, require configured copied win rate/PF and
    cool down only after the configured consecutive-loss streak.
    """
    m = _guard._copied_metrics(app, tid, wallet)
    key = f"sol_profit_guard_suspend:{tid}:{wallet}"
    now = int(time.time())

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        raw = _sol._state(conn, key, "") or ""
        try:
            state = json.loads(raw) if raw else {}
        except Exception:
            state = {}
        until = int(state.get("until") or 0)
        seen = int(state.get("latest_closed_at") or 0)
        if now < until:
            return False

        limit = max(1, _sol._int(cfg.get("max_consecutive_copied_losses"), 3))
        latest = int(m.get("latest_closed_at") or 0)
        if int(m.get("consecutive_losses") or 0) >= limit and latest and latest != seen:
            until = now + max(5, _sol._int(cfg.get("leader_suspend_minutes"), 180)) * 60
            _sol._set_state(
                conn,
                key,
                json.dumps({"until": until, "latest_closed_at": latest}),
            )
            return False

    min_copied = max(1, _sol._int(cfg.get("min_copied_trades_for_guard"), 5))
    if int(m.get("closed") or 0) >= min_copied:
        if _sol._dec(m.get("win_rate"), 0) < _sol._dec(cfg.get("min_copied_win_rate_pct"), 40):
            return False
        if _sol._dec(m.get("profit_factor"), 0) < _sol._dec(cfg.get("min_copied_profit_factor"), 1):
            return False
    return True


def install():
    if getattr(_sol, "_first_day_strategy_restore_installed", False):
        return

    # Restore policy behaviour only.  The live executor and close path remain the
    # current audited/safe implementations installed by the later safety patches.
    _sol.settings = settings_first_day_strategy
    _guard._copied_ok = copied_ok_first_day
    _sol.process_leader_event = _live.process_leader_event
    _sol._validate_shadow_entry = validate_entry_positive_executable_edge

    _sol._first_day_strategy_restore_installed = True
    print(
        "[solana-first-day-strategy] restored=true reference=%s leaders=5 "
        "signal_age=30s roundtrip<=3%% entry_deterioration<=2%% positive_executable_edge>=0.25%% "
        "stop=10%% tp=25%% leader_sell=original_gated execution_safety=current"
        % FIRST_DAY_REFERENCE_COMMIT[:12]
    )


install()
