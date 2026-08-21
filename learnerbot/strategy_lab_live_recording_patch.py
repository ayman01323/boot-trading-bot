from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import auto_trader as _auto
from . import execution_queue as _queue
from . import sibot as _sibot
from . import strategy_lab as _lab

# Strategy Lab is deliberately never allowed to arm LIVE trading, change capital or
# bypass an execution gate (see strategy_lab.py's own docstring and
# strategy_source_contract.py's enforced research_only/automatic_execution_allowed
# flags). This module only feeds it real evidence from the two engines that are
# already LIVE today -- SiBot leader-copy (close_position) and the direct-market
# arbitrage engine (auto_trade_execution.csv) -- so its REPLACE/REWORK/PROMOTION_
# CANDIDATE evaluations reflect actual trading history instead of being permanently
# empty. It does not itself decide anything; sibot_strategy_lab_throttle_patch.py is
# the separate, narrowly-scoped consumer that acts on this evidence.

_LEADER_COPY_CACHE: dict[str, str] = {}
_MARKET_NATIVE_CACHE: dict[str, str] = {}

_PREV_CLOSE_POSITION = _sibot.close_position
_PREV_QUEUE_RECOMMENDATIONS = _queue.queue_armed_recommendations


def leader_copy_strategy_id(app, chain_slug: str) -> str:
    key = str(chain_slug)
    sid = _LEADER_COPY_CACHE.get(key)
    if sid:
        return sid
    reg = _lab.register_strategy(
        app,
        name=f"SiBot Leader Copy — {key}",
        family="LEADER_COPY",
        source="LEADER_COPY",
        hypothesis="Mirror top-ranked EVM wallets' realised BUY/SELL cycles on this chain, gated by sibot_profit_guard_patch's quality/risk checks.",
        params={"chain_slug": key},
        proposed_by="strategy_lab_live_recording_patch",
    )
    sid = reg["strategy_id"]
    _LEADER_COPY_CACHE[key] = sid
    return sid


def _market_native_strategy_id(app, chain_slug: str) -> str:
    key = str(chain_slug)
    sid = _MARKET_NATIVE_CACHE.get(key)
    if sid:
        return sid
    reg = _lab.register_strategy(
        app,
        name=f"Direct Market Arbitrage — {key}",
        family="MARKET_NATIVE",
        source="MARKET_NATIVE",
        hypothesis="Execute V2 pool-graph cycles on this chain that clear profit, liquidity and simulation gates independent of any leader wallet.",
        params={"chain_slug": key},
        proposed_by="strategy_lab_live_recording_patch",
    )
    sid = reg["strategy_id"]
    _MARKET_NATIVE_CACHE[key] = sid
    return sid


def _hour_bounds(ts: int) -> tuple[int, int]:
    start = ts - (ts % 3600)
    return start, start + 3600


def _accumulate_and_record(app, strategy_id: str, mode: str, window_start: int, window_end: int, *,
                            trades: int, wins: int, losses: int, gross_profit: Decimal, gross_loss: Decimal) -> None:
    mode = str(mode or "LIVE").upper()
    with _sibot._DB_LOCK:
        with closing(_lab.connect(app)) as conn:
            row = conn.execute(
                "SELECT * FROM strategy_lab_windows WHERE strategy_id=? AND window_start=? AND window_end=? AND mode=?",
                (str(strategy_id), int(window_start), int(window_end), mode),
            ).fetchone()
        prev = dict(row) if row else {}

        def pd(key: str) -> Decimal:
            try:
                return Decimal(str(prev.get(key) or "0"))
            except Exception:
                return Decimal(0)

        new_trades = int(prev.get("trades") or 0) + trades
        new_wins = int(prev.get("wins") or 0) + wins
        new_losses = int(prev.get("losses") or 0) + losses
        new_gross_profit = pd("gross_profit") + gross_profit
        new_gross_loss = pd("gross_loss") + gross_loss
        new_largest_loss = max(pd("largest_loss"), gross_loss)
        _lab.record_window(
            app, strategy_id,
            window_start=window_start, window_end=window_end, mode=mode,
            opportunities=new_trades, eligible_opportunities=new_trades, trades=new_trades,
            wins=new_wins, losses=new_losses,
            gross_profit=new_gross_profit, gross_loss=new_gross_loss,
            net_profit=new_gross_profit - new_gross_loss,
            largest_loss=new_largest_loss,
        )


def close_position_with_strategy_lab(app, position_id: str, fraction=Decimal(1), reason="EXIT") -> dict:
    with closing(_sibot.connect(app)) as conn:
        before = conn.execute(
            "SELECT chain_slug,mode FROM positions WHERE position_id=?", (position_id,)
        ).fetchone()
    result = _PREV_CLOSE_POSITION(app, position_id, fraction, reason)
    if not before or not result.get("closed"):
        return result
    try:
        with closing(_sibot.connect(app)) as conn:
            row = conn.execute(
                "SELECT realised_net_native FROM positions WHERE position_id=?", (position_id,)
            ).fetchone()
        net = Decimal(str(row["realised_net_native"])) if row else Decimal(str(result.get("realised_net_native") or 0))
        sid = leader_copy_strategy_id(app, str(before["chain_slug"]))
        now = int(time.time())
        window_start, window_end = _hour_bounds(now)
        _accumulate_and_record(
            app, sid, str(before["mode"] or "SHADOW"), window_start, window_end,
            trades=1, wins=1 if net > 0 else 0, losses=1 if net < 0 else 0,
            gross_profit=max(Decimal(0), net), gross_loss=max(Decimal(0), -net),
        )
    except Exception:
        pass
    return result


def _market_native_cursor_path(app) -> Path:
    return Path(app.data_dir) / "strategy_lab_market_native_cursor.json"


def _load_cursor(app) -> dict:
    path = _market_native_cursor_path(app)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_cursor(app, cursor: dict) -> None:
    path = _market_native_cursor_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor), encoding="utf-8")


_EXECUTED_STATUSES = {"SUCCESS", "SUCCESS_FEE_PENDING", "BROADCAST"}


def _record_market_native_progress(app) -> None:
    path = Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"
    if not path.exists():
        return
    rows = _auto._rows(path)
    if not rows:
        return
    cursor = _load_cursor(app)
    buckets: dict[tuple[str, int, int], dict] = {}
    max_ts: dict[str, int] = {}
    for r in rows:
        slug = str(r.get("chain_slug") or "").strip().lower()
        if not slug:
            continue
        try:
            ts = int(float(r.get("timestamp_epoch") or 0))
        except Exception:
            continue
        watermark = int(cursor.get(slug) or 0)
        if ts <= watermark:
            continue
        max_ts[slug] = max(max_ts.get(slug, watermark), ts)
        status = str(r.get("status") or "").upper()
        if status not in _EXECUTED_STATUSES:
            continue
        net_raw = r.get("realised_net_base") or r.get("expected_net_base") or "0"
        try:
            net = Decimal(str(net_raw))
        except Exception:
            net = Decimal(0)
        wstart, wend = _hour_bounds(ts)
        key = (slug, wstart, wend)
        b = buckets.setdefault(key, {"trades": 0, "wins": 0, "losses": 0, "gross_profit": Decimal(0), "gross_loss": Decimal(0)})
        b["trades"] += 1
        if net > 0:
            b["wins"] += 1
            b["gross_profit"] += net
        elif net < 0:
            b["losses"] += 1
            b["gross_loss"] += -net

    for (slug, wstart, wend), b in buckets.items():
        sid = _market_native_strategy_id(app, slug)
        _accumulate_and_record(
            app, sid, "LIVE", wstart, wend,
            trades=b["trades"], wins=b["wins"], losses=b["losses"],
            gross_profit=b["gross_profit"], gross_loss=b["gross_loss"],
        )
    if max_ts:
        cursor.update(max_ts)
        _save_cursor(app, cursor)


def queue_armed_recommendations_with_strategy_lab(app, recommendations):
    result = _PREV_QUEUE_RECOMMENDATIONS(app, recommendations)
    try:
        _record_market_native_progress(app)
    except Exception:
        pass
    return result


def install():
    if getattr(_sibot, "_strategy_lab_recording_installed", False):
        return
    _sibot.close_position = close_position_with_strategy_lab
    _queue.queue_armed_recommendations = queue_armed_recommendations_with_strategy_lab
    _sibot._strategy_lab_recording_installed = True


install()
