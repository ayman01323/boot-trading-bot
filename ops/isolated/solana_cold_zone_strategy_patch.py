from __future__ import annotations

"""Isolated Google learner: COLD ZONE 17-Aug opportunity policy.

This patch is intentionally imported only by the isolated learner runtime under
/home/ayman01323/BOOT/testingbots/learn.  It restores the simple 17-Aug leader
selector, keeps current signed execution/simulation machinery, classifies modern
PoolCheck evidence as HARD vs warning, and applies the owner's 45/50 minute
high-risk pool lifecycle.
"""

import html
import json
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal

import requests

from . import solana_live_patch as _live
from . import solana_pool_risk_gate as _pool
from . import solana_sibot as _sol
from .solana_live_executor import SolanaLiveExecutor, SolanaLivePostExecutionError
from .user_registry import all_users, user_bool

PROFILE = "COLD_ZONE_17AUG_V1"
TARGET_NET_PCT = Decimal("5")
ENTRY_POOL_AGE_LIMIT_SECONDS = 45 * 60
LOSS_GRACE_SECONDS = 5 * 60
FORCED_EXIT_POOL_AGE_SECONDS = 50 * 60
MAX_ACTIVE_POSITIONS = 10
WRITE_OFF_REMINDER_SECONDS = 30 * 60
EXIT_RETRY_SECONDS = 60
SALE_FAILURE_NOTICE_SECONDS = 5 * 60
DEX_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{mint}"

# Only high-confidence capital-safety evidence remains an external hard block.
# Young-pool, LP-lock/concentration and provider/indexing evidence remains visible
# as warning telemetry but cannot by itself suppress a 0.0005 SOL canary.
_EXTERNAL_HARD_CODES = {
    "TOKEN_SECURITY_SEVERE",
    "POOL_LIQUIDITY_COLLAPSE",
}

_PREV_SETTINGS = _sol.settings
_PREV_REFRESH_RANKINGS = _sol.refresh_rankings
_PREV_VALIDATE = _sol._validate_shadow_entry
_PREV_PROCESS = _sol.process_leader_event
_PREV_MONITOR = _sol.monitor_positions

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS cold_zone_positions(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  mint TEXT NOT NULL,
  pool_created_at INTEGER NOT NULL,
  pool_age_at_entry_seconds INTEGER NOT NULL,
  target_net_pct TEXT NOT NULL,
  required_gross_pct TEXT,
  expected_gross_pct TEXT,
  estimated_cost_pct TEXT,
  warning_codes TEXT,
  liquidity_failures INTEGER NOT NULL DEFAULT 0,
  first_liquidity_failure_at INTEGER,
  last_liquidity_failure_at INTEGER,
  last_sale_failure_notice_at INTEGER,
  last_exit_attempt_at INTEGER,
  rug_suspected INTEGER NOT NULL DEFAULT 0,
  rug_reason TEXT,
  last_writeoff_notice_at INTEGER,
  grace_notice_sent INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cold_zone_user ON cold_zone_positions(telegram_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS cold_zone_notifications(
  notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  position_id TEXT,
  mint TEXT,
  message_html TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cold_zone_decisions(
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  telegram_id TEXT,
  leader_wallet TEXT,
  leader_signature TEXT,
  mint TEXT,
  decision TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reason TEXT NOT NULL,
  details_json TEXT
);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def _i(value, default=0) -> int:
    return _sol._int(value, default)


def _now() -> int:
    return int(time.time())


def settings_cold_zone(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg.update(
        {
            "solana_strategy_profile": PROFILE,
            "leaders_per_user": "2",
            "min_closed_trades": "5",
            "min_win_rate_pct": "50",
            "require_complete_history": "false",
            "max_signal_age_seconds": "30",
            "max_roundtrip_loss_pct": "3",
            "max_entry_deterioration_pct": "2",
            "live_trade_sol": "0.0005",
            "live_min_sol_reserve": "0.005",
            "live_max_positions": "10",
            "mirror_partial_sells": "false",
            "cold_zone_entry_pool_age_seconds": str(ENTRY_POOL_AGE_LIMIT_SECONDS),
            "cold_zone_forced_exit_pool_age_seconds": str(FORCED_EXIT_POOL_AGE_SECONDS),
            "cold_zone_target_net_pct": str(TARGET_NET_PCT),
        }
    )
    return cfg


def _queue_notice(app, tid: str, kind: str, message: str, position_id: str = "", mint: str = "") -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _ensure(conn)
            conn.execute(
                "INSERT INTO cold_zone_notifications(telegram_id,kind,position_id,mint,message_html,created_at) VALUES(?,?,?,?,?,?)",
                (str(tid), str(kind), str(position_id), str(mint), str(message), _now()),
            )
            conn.commit()
    except Exception:
        pass


def _decision(app, tid: str, event: dict, decision: str, code: str, reason: str, details: dict | None = None) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _ensure(conn)
            conn.execute(
                """INSERT INTO cold_zone_decisions(
                     created_at,telegram_id,leader_wallet,leader_signature,mint,decision,reason_code,reason,details_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    _now(), str(tid), str(event.get("leader_wallet") or ""), str(event.get("signature") or ""),
                    str(event.get("mint") or ""), str(decision), str(code), str(reason)[:1000],
                    json.dumps(details or {}, default=str, separators=(",", ":"))[:5000],
                ),
            )
            conn.commit()
    except Exception:
        pass


def refresh_rankings_17aug(app, telegram_id=None):
    """Exact shape of the 17-Aug leader selector: positive P&L + 5 trades + 50% wins."""
    cfg = settings_cold_zone(app)
    lookback = max(1, min(365, _i(cfg.get("lookback_days"), 60)))
    cutoff = _now() - lookback * 86400
    min_closed = max(1, _i(cfg.get("min_closed_trades"), 5))
    min_win = max(0.0, min(100.0, float(cfg.get("min_win_rate_pct") or 50)))
    leaders_n = max(1, min(10, _i(cfg.get("leaders_per_user"), 2)))
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if telegram_id is not None:
        users = [u for u in users if str(u.get("telegram_id")) == str(telegram_id)]

    with closing(_sol.connect(app)) as conn:
        rows = conn.execute("SELECT wallet,net_sol FROM trades WHERE sell_ts>=?", (cutoff,)).fetchall()

    agg = defaultdict(lambda: {"profit": Decimal(0), "loss": Decimal(0), "net": Decimal(0), "wins": 0, "losses": 0, "closed": 0})
    for row in rows:
        a = agg[str(row["wallet"])]
        n = _d(row["net_sol"], 0)
        a["net"] += n
        a["closed"] += 1
        if n > 0:
            a["profit"] += n
            a["wins"] += 1
        elif n < 0:
            a["loss"] += -n
            a["losses"] += 1

    ranked = []
    for wallet, a0 in agg.items():
        if not (a0["net"] > 0 and a0["profit"] > a0["loss"]):
            continue
        a = dict(a0)
        a["wallet"] = wallet
        a["win_rate"] = a["wins"] / a["closed"] * 100.0 if a["closed"] else 0.0
        ranked.append(a)
    ranked.sort(key=lambda x: (x["net"], x["profit"], x["closed"], x["win_rate"]), reverse=True)
    top = ranked[:20]
    now = _now()

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        for user in users:
            tid = str(user.get("telegram_id") or "")
            if not tid:
                continue
            conn.execute("DELETE FROM rankings WHERE telegram_id=?", (tid,))
            for rank, a in enumerate(top, 1):
                conn.execute(
                    """INSERT INTO rankings(telegram_id,lookback_days,rank,wallet,gross_profit_sol,gross_loss_sol,net_profit_sol,
                                              wins,losses,closed_trades,win_rate,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, lookback, rank, a["wallet"], str(a["profit"]), str(a["loss"]), str(a["net"]),
                     a["wins"], a["losses"], a["closed"], a["win_rate"], now),
                )
            old = {str(r["wallet"]): int(r["selected_at"] or now) for r in conn.execute(
                "SELECT wallet,selected_at FROM leaders WHERE telegram_id=?", (tid,)
            ).fetchall()}
            conn.execute("DELETE FROM leaders WHERE telegram_id=?", (tid,))
            safe = [a for a in top if a["closed"] >= min_closed and a["win_rate"] >= min_win]
            for rank, a in enumerate(safe[:leaders_n], 1):
                conn.execute(
                    "INSERT INTO leaders(telegram_id,rank,wallet,net_profit_sol,win_rate,closed_trades,selected_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (tid, rank, a["wallet"], str(a["net"]), a["win_rate"], a["closed"], old.get(a["wallet"], now), now),
                )
            conn.commit()
    _sol.export_csv(app)
    return top


def _dex_pairs(mint: str, timeout: float = 2.5) -> list[dict]:
    r = requests.get(
        DEX_URL.format(mint=str(mint)),
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "boot-cold-zone/1.0"},
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("DexScreener returned invalid pool data")
    return [p for p in data if isinstance(p, dict) and str(p.get("chainId") or "solana").lower() == "solana"]


def _liq_usd(pair: dict) -> Decimal:
    return max(Decimal(0), _d((pair.get("liquidity") or {}).get("usd"), 0))


def _pair_created(pair: dict) -> int | None:
    try:
        raw = int(float(pair.get("pairCreatedAt") or 0))
    except Exception:
        return None
    if raw <= 0:
        return None
    return int(raw / 1000) if raw > 10_000_000_000 else raw


def _pool_clock(mint: str, cfg: dict) -> tuple[int | None, int | None, list[dict], list[str]]:
    warnings: list[str] = []
    try:
        pairs = _dex_pairs(mint, float(max(Decimal("0.5"), min(Decimal("3"), _d(cfg.get("live_pool_external_timeout_seconds"), "2.5")))))
    except Exception as exc:
        return None, None, [], [f"DEX_UNAVAILABLE:{type(exc).__name__}"]
    if not pairs:
        return None, None, pairs, ["DEX_INDEX_PENDING"]

    max_liq = max((_liq_usd(p) for p in pairs), default=Decimal(0))
    floor = max(Decimal("100"), max_liq * Decimal("0.10")) if max_liq > 0 else Decimal("100")
    material = [p for p in pairs if _liq_usd(p) >= floor]
    if not material:
        material = [max(pairs, key=_liq_usd)]
    created = [v for v in (_pair_created(p) for p in material) if v]
    if not created:
        return None, None, pairs, ["POOL_AGE_UNKNOWN"]

    # Use the oldest material pool, not the newest tiny pair, so an old token can
    # never masquerade as a newly launched COLD ZONE opportunity.
    pool_created = min(created)
    age = max(0, _now() - pool_created)
    return pool_created, age, pairs, warnings


def _pool_hard_vs_warning(app, event: dict, cfg: dict, pairs: list[dict]) -> tuple[bool, list[str], dict]:
    warnings: list[str] = []
    evidence: dict = {}
    mint = str(event.get("mint") or "")

    try:
        ext = dict(_pool.external_pool_check(mint, cfg) or {})
        code = str(ext.get("reason_code") or "POOLCHECK_UNKNOWN")
        decision = str(ext.get("decision") or "HARD_BLOCK").upper()
        evidence["external_poolcheck"] = ext
        if code in _EXTERNAL_HARD_CODES:
            return False, warnings, evidence
        if decision != "PASS":
            warnings.append(code)
    except Exception as exc:
        warnings.append(f"POOLCHECK_UNAVAILABLE:{type(exc).__name__}")

    try:
        dex = dict(_pool.evaluate_dexscreener(pairs, cfg, mint=mint, now_epoch=time.time()) or {})
        code = str(dex.get("reason_code") or "DEX_POOL_UNKNOWN")
        evidence["dex_poolcheck"] = dex
        if code == "POOL_LIQUIDITY_COLLAPSE":
            return False, warnings, evidence
        if str(dex.get("decision") or "").upper() != "PASS":
            warnings.append(code)
    except Exception as exc:
        warnings.append(f"DEX_RISK_EVAL_UNAVAILABLE:{type(exc).__name__}")

    return True, sorted(set(warnings)), evidence


def _cold_preflight(app, event: dict, allocation: Decimal, cfg: dict) -> tuple[bool, str, dict]:
    age = max(0, _now() - _i(event.get("event_ts"), 0))
    maximum = max(1, _i(cfg.get("max_signal_age_seconds"), 30))
    if age > maximum:
        return False, f"stale leader signal {age}s > {maximum}s", {"signal_age_seconds": age}

    lamports = int(Decimal(allocation) * Decimal(1_000_000_000))
    try:
        buy = _sol.jupiter_quote(app, _sol.WSOL_MINT, str(event.get("mint") or ""), lamports)
        out_raw = _i(buy.get("outAmount") or buy.get("outputAmount"), 0)
        if out_raw <= 0:
            return False, "Jupiter BUY quote returned no token output", {}
        back = _sol.jupiter_quote(app, str(event.get("mint") or ""), _sol.WSOL_MINT, out_raw)
        back_lamports = _i(back.get("outAmount") or back.get("outputAmount"), 0)
    except Exception as exc:
        return False, f"actual-size BUY→SELL liquidity quote failed: {type(exc).__name__}: {str(exc)[:220]}", {}
    if back_lamports <= 0:
        return False, "actual-size reverse SELL quote returned no SOL", {"out_raw": out_raw}

    back_sol = Decimal(back_lamports) / Decimal(1_000_000_000)
    roundtrip = max(Decimal(0), (Decimal(1) - back_sol / Decimal(allocation)) * Decimal(100))
    limit = max(Decimal(0), _d(cfg.get("max_roundtrip_loss_pct"), 3))
    if roundtrip > limit:
        return False, f"actual 0.0005-size BUY→SELL loss {roundtrip:.3f}% > {limit:.3f}%", {
            "out_raw": out_raw, "roundtrip_loss_pct": roundtrip,
        }

    leader_sol = _d(event.get("sol_amount"), 0)
    leader_raw = Decimal(max(0, _i(event.get("token_amount_raw"), 0)))
    deterioration = Decimal(0)
    if leader_sol > 0 and leader_raw > 0 and out_raw > 0:
        leader_raw_per_sol = leader_raw / leader_sol
        ours_raw_per_sol = Decimal(out_raw) / Decimal(allocation)
        deterioration = max(Decimal(0), (leader_raw_per_sol / ours_raw_per_sol - Decimal(1)) * Decimal(100))
    det_limit = max(Decimal(0), _d(cfg.get("max_entry_deterioration_pct"), 2))
    if deterioration > det_limit:
        return False, f"entry deterioration {deterioration:.3f}% > {det_limit:.3f}%", {
            "out_raw": out_raw, "roundtrip_loss_pct": roundtrip, "deterioration_pct": deterioration,
        }
    return True, "PASS", {
        "out_raw": out_raw,
        "back_lamports": back_lamports,
        "roundtrip_loss_pct": roundtrip,
        "deterioration_pct": deterioration,
    }


def _leader_available_gross(app, wallet: str, cfg: dict) -> dict:
    lookback = max(1, min(365, _i(cfg.get("lookback_days"), 60)))
    cutoff = _now() - lookback * 86400
    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            "SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
            (str(wallet), cutoff),
        ).fetchall()
    returns: list[Decimal] = []
    for row in rows:
        cost = _d(row["cost_sol"], 0)
        if cost <= 0:
            continue
        pct = _d(row["net_sol"], 0) * Decimal(100) / cost
        returns.append(max(Decimal("-50"), min(Decimal("100"), pct)))
    recent = returns[-10:]
    mean_all = sum(returns, Decimal(0)) / Decimal(len(returns)) if returns else Decimal(0)
    mean_recent = sum(recent, Decimal(0)) / Decimal(len(recent)) if recent else Decimal(0)
    available = max(Decimal(0), min(mean_all, mean_recent)) if returns and recent else Decimal(0)
    return {
        "samples": len(returns),
        "recent_samples": len(recent),
        "historical_mean_gross_pct": mean_all,
        "recent_mean_gross_pct": mean_recent,
        "available_gross_pct": available,
    }


def _estimated_network_fee_pct(executor: SolanaLiveExecutor, mint: str, allocation: Decimal, cfg: dict) -> tuple[Decimal, dict]:
    lamports = int(Decimal(allocation) * Decimal(1_000_000_000))
    per_leg = 5_000
    detail = {"fee_source": "fallback_base_fee", "estimated_fee_lamports_per_leg": per_leg}
    try:
        order = executor._order(_sol.WSOL_MINT, str(mint), lamports)
        base = max(0, _i(order.get("signatureFeeLamports"), 5_000))
        priority = max(0, _i(order.get("prioritizationFeeLamports"), 0))
        bps = max(Decimal(0), _d(((order.get("platformFee") or {}).get("feeBps")), 0))
        platform = int((Decimal(lamports) * bps / Decimal(10_000))) if bps > 0 else 0
        per_leg = max(5_000, base + priority + platform)
        detail = {
            "fee_source": "live_jupiter_order",
            "base_fee_lamports": base,
            "priority_fee_lamports": priority,
            "platform_fee_equiv_lamports": platform,
            "estimated_fee_lamports_per_leg": per_leg,
        }
    except Exception as exc:
        detail["fee_quote_warning"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    pct = Decimal(per_leg * 2) * Decimal(100) / Decimal(max(1, lamports))
    return pct, detail


def _profit_test(app, event: dict, allocation: Decimal, cfg: dict, preflight: dict, executor: SolanaLiveExecutor) -> tuple[bool, str, dict]:
    leader = _leader_available_gross(app, str(event.get("leader_wallet") or ""), cfg)
    roundtrip = max(Decimal(0), _d(preflight.get("roundtrip_loss_pct"), 100))
    network_pct, fee_detail = _estimated_network_fee_pct(executor, str(event.get("mint") or ""), allocation, cfg)
    slippage_bps = max(Decimal(0), _d(cfg.get("live_order_slippage_bps"), 50))
    slippage_reserve_pct = slippage_bps * Decimal(2) / Decimal(100)
    costs = roundtrip + network_pct + slippage_reserve_pct
    required = TARGET_NET_PCT + costs
    available = _d(leader.get("available_gross_pct"), 0)
    expected_net = available - costs
    detail = {
        **leader,
        **fee_detail,
        "roundtrip_loss_pct": roundtrip,
        "estimated_network_fee_pct": network_pct,
        "slippage_reserve_pct": slippage_reserve_pct,
        "estimated_total_cost_pct": costs,
        "target_net_pct": TARGET_NET_PCT,
        "required_gross_pct": required,
        "expected_net_pct": expected_net,
    }
    if int(leader.get("samples") or 0) < 5:
        return False, f"leader profit evidence has {int(leader.get('samples') or 0)} samples; need 5", detail
    if available < required:
        return False, f"available gross {available:.3f}% cannot cover costs and leave {TARGET_NET_PCT:.2f}% net; need {required:.3f}% gross", detail
    return True, "PASS_5_PERCENT_NET_TEST", detail


def _active_open_count(app, tid: str) -> int:
    with closing(_sol.connect(app)) as conn:
        _ensure(conn)
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
        rows = [dict(r) for r in conn.execute(
            "SELECT p.position_id" + (",p.liquidity_state" if "liquidity_state" in cols else "") +
            ",COALESCE(c.rug_suspected,0) rug_suspected FROM positions p LEFT JOIN cold_zone_positions c ON c.position_id=p.position_id "
            "WHERE p.telegram_id=? AND p.status='OPEN' AND p.mode='LIVE'",
            (str(tid),),
        ).fetchall()]
    active = 0
    for row in rows:
        if int(row.get("rug_suspected") or 0):
            continue
        if str(row.get("liquidity_state") or "").upper() == "LIQUIDITY_STUCK":
            continue
        active += 1
    return active


def _remember_position(app, tid: str, pid: str, mint: str, pool_created: int, pool_age: int, profit: dict, warnings: list[str]) -> None:
    now = _now()
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure(conn)
        conn.execute(
            """INSERT OR REPLACE INTO cold_zone_positions(
                 position_id,telegram_id,mint,pool_created_at,pool_age_at_entry_seconds,target_net_pct,
                 required_gross_pct,expected_gross_pct,estimated_cost_pct,warning_codes,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(pid), str(tid), str(mint), int(pool_created), int(pool_age), str(TARGET_NET_PCT),
                str(profit.get("required_gross_pct") or ""), str(profit.get("available_gross_pct") or ""),
                str(profit.get("estimated_total_cost_pct") or ""), ",".join(sorted(set(warnings))), now, now,
            ),
        )
        conn.commit()


def _entry_rejection_message(pool_age: int | None, reason: str, profit: dict | None = None, warnings: list[str] | None = None) -> str:
    lines = ["❌ <b>COLD ZONE BUY REFUSED</b>"]
    if pool_age is not None:
        lines.append(f"Pool age: <b>{pool_age // 60}m {pool_age % 60}s</b>")
    lines.append(f"Reason: <code>{html.escape(str(reason)[:700])}</code>")
    if profit:
        available = _d(profit.get("available_gross_pct"), 0)
        costs = _d(profit.get("estimated_total_cost_pct"), 0)
        required = _d(profit.get("required_gross_pct"), TARGET_NET_PCT + costs)
        expected_net = _d(profit.get("expected_net_pct"), available - costs)
        shortfall = max(Decimal(0), required - available)
        lines.extend(
            [
                f"Requested net profit: <b>{TARGET_NET_PCT:.2f}%</b>",
                f"Estimated total costs/reserves: <b>{costs:.3f}%</b>",
                f"Available leader gross return: <b>{available:.3f}%</b>",
                f"Expected net after costs: <b>{expected_net:.3f}%</b>",
                f"Minimum gross profit required to pass: <b>{required:.3f}%</b>",
                f"Gross-profit shortfall: <b>{shortfall:.3f} percentage points</b>",
            ]
        )
    if warnings:
        lines.append("Warnings (not blockers): <code>%s</code>" % html.escape(", ".join(sorted(set(warnings)))[:700]))
    return "\n".join(lines)


def process_leader_event_cold_zone(app, event: dict):
    cfg = settings_cold_zone(app)
    action = str(event.get("action") or "").upper()
    if action == "SELL":
        return [{"action": "IGNORE", "reason": "COLD_ZONE ignores leader SELL signals"}]
    if action != "BUY":
        return []

    actions = []
    for user in all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not user_bool(app.csv_dir, tid, _sol.SOLANA_CHAIN_ID, "learner_new_entries_enabled", False):
            continue
        try:
            if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
                continue
        except Exception:
            continue
        rank = _sol._leader_rank(app, tid, str(event.get("leader_wallet") or ""))
        if rank is None:
            continue

        mint = str(event.get("mint") or "")
        if _sol._open_position(app, tid, mint):
            actions.append({"telegram_id": tid, "action": "SKIP", "reason": "same mint already held"})
            continue
        if _active_open_count(app, tid) >= MAX_ACTIVE_POSITIONS:
            reason = f"COLD ZONE active position limit reached ({MAX_ACTIVE_POSITIONS})"
            _decision(app, tid, event, "REJECT", "POSITION_LIMIT", reason)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue

        allocation, reserve = _live.live_limits(app, tid, cfg)
        allocation = Decimal("0.0005")
        reserve = max(Decimal("0.005"), Decimal(reserve))

        pool_created, pool_age, pairs, clock_warnings = _pool_clock(mint, cfg)
        if pool_created is None or pool_age is None:
            reason = "pool start time could not be proved; COLD ZONE requires a confirmed 0–45 minute pool"
            _decision(app, tid, event, "REJECT", "POOL_AGE_UNKNOWN", reason, {"warnings": clock_warnings})
            _queue_notice(app, tid, "BUY_REFUSED", _entry_rejection_message(None, reason, warnings=clock_warnings), mint=mint)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue
        if pool_age > ENTRY_POOL_AGE_LIMIT_SECONDS:
            reason = f"pool age {pool_age // 60}m {pool_age % 60}s is outside COLD ZONE 0–45m entry window"
            _decision(app, tid, event, "REJECT", "POOL_TOO_OLD", reason, {"pool_age_seconds": pool_age})
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue

        pool_ok, pool_warnings, pool_evidence = _pool_hard_vs_warning(app, event, cfg, pairs)
        warnings = sorted(set(clock_warnings + pool_warnings))
        if not pool_ok:
            reason = "PoolCheck found high-confidence capital-safety evidence"
            _decision(app, tid, event, "HARD_BLOCK", "POOL_HARD_BLOCK", reason, pool_evidence)
            _queue_notice(app, tid, "BUY_REFUSED", _entry_rejection_message(pool_age, reason, warnings=warnings), mint=mint)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue

        ok, reason, preflight = _cold_preflight(app, event, allocation, cfg)
        if not ok:
            _decision(app, tid, event, "HARD_BLOCK", "ACTUAL_SIZE_PREFLIGHT", reason, preflight)
            _queue_notice(app, tid, "BUY_REFUSED", _entry_rejection_message(pool_age, reason, warnings=warnings), mint=mint)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue

        try:
            executor = SolanaLiveExecutor(app, tid)
            need = int((allocation + reserve) * Decimal(1_000_000_000))
            if executor.native_balance_lamports() < need:
                reason = f"insufficient SOL for 0.0005 SOL trade plus {reserve} SOL reserve"
                _decision(app, tid, event, "REJECT", "INSUFFICIENT_RESERVE", reason)
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                continue
        except Exception as exc:
            reason = f"signing/funding preflight unavailable: {type(exc).__name__}: {str(exc)[:220]}"
            _decision(app, tid, event, "HARD_BLOCK", "SIGNING_OR_FUNDING", reason)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
            continue

        profit_ok, profit_reason, profit = _profit_test(app, event, allocation, cfg, preflight, executor)
        if not profit_ok:
            _decision(app, tid, event, "REJECT", "NET_PROFIT_TEST", profit_reason, profit)
            _queue_notice(app, tid, "BUY_REFUSED", _entry_rejection_message(pool_age, profit_reason, profit, warnings), mint=mint)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": profit_reason})
            continue

        claimed, attempt_key = _live._claim_attempt(app, tid, event)
        if not claimed:
            actions.append({"telegram_id": tid, "action": "SKIP", "reason": "duplicate leader signal already attempted"})
            continue
        try:
            trade = executor.buy(mint, allocation, reserve)
            _live._update_attempt(app, attempt_key, "EXECUTED", trade)
            pid, out_raw, entry_cost = _live._insert_live_position(app, tid, rank, event, trade, allocation, cfg)
            _remember_position(app, tid, pid, mint, pool_created, pool_age, profit, warnings)
            sig = str(trade.get("signature") or "")
            message = (
                "🚀 <b>COLD ZONE BUY CONFIRMED</b>\n"
                f"Pool age at entry: <b>{pool_age // 60}m {pool_age % 60}s</b>\n"
                f"True wallet spend: <b>{entry_cost:.9f} SOL</b>\n"
                f"Target true net: <b>+{TARGET_NET_PCT:.2f}%</b>\n"
                f"Required gross at entry: <b>{_d(profit.get('required_gross_pct'), 0):.3f}%</b>\n"
                f"Token: <code>{html.escape(mint)}</code>\n"
                f"TX: <code>{html.escape(sig)}</code>"
            )
            if warnings:
                message += "\nWarnings (allowed): <code>%s</code>" % html.escape(", ".join(warnings)[:700])
            _queue_notice(app, tid, "BUY_CONFIRMED", message, pid, mint)
            _decision(app, tid, event, "OPEN", "COLD_ZONE_OPEN", "COLD ZONE entry executed", {**preflight, **profit, "warnings": warnings})
            actions.append({"telegram_id": tid, "action": "BUY", "position_id": pid, "signature": sig})
        except SolanaLivePostExecutionError as exc:
            _live._update_attempt(app, attempt_key, "LANDED_INVALID_OUTPUT", exc.result, str(exc))
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc), "signature": exc.signature})
        except Exception as exc:
            _live._update_attempt(app, attempt_key, "FAILED_NO_RETRY", None, str(exc))
            reason = f"LIVE BUY failed: {type(exc).__name__}: {str(exc)[:350]}"
            _queue_notice(app, tid, "BUY_REFUSED", _entry_rejection_message(pool_age, reason, profit, warnings), mint=mint)
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
    return actions


def _cold_row(conn, pid: str) -> dict | None:
    _ensure(conn)
    row = conn.execute("SELECT * FROM cold_zone_positions WHERE position_id=?", (str(pid),)).fetchone()
    return dict(row) if row else None


def _update_cold(app, pid: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure(conn)
        keys = list(fields)
        conn.execute(
            "UPDATE cold_zone_positions SET " + ",".join(f"{k}=?" for k in keys) + " WHERE position_id=?",
            [fields[k] for k in keys] + [str(pid)],
        )
        conn.commit()


def _failure_state(app, p: dict, cold: dict, reason: str) -> None:
    now = _now()
    failures = int(cold.get("liquidity_failures") or 0) + 1
    first = int(cold.get("first_liquidity_failure_at") or 0) or now
    rug = failures >= 3 and now - first >= 60
    fields = {
        "liquidity_failures": failures,
        "first_liquidity_failure_at": first,
        "last_liquidity_failure_at": now,
    }
    if rug:
        fields.update({"rug_suspected": 1, "rug_reason": str(reason)[:900]})
    last_notice = int(cold.get("last_sale_failure_notice_at") or 0)
    if now - last_notice >= SALE_FAILURE_NOTICE_SECONDS:
        fields["last_sale_failure_notice_at"] = now
        _queue_notice(
            app,
            str(p.get("telegram_id") or ""),
            "SALE_FAILED",
            "🚨 <b>COLD ZONE SALE DID NOT HAPPEN</b>\n"
            f"Position: <code>{html.escape(str(p.get('position_id') or ''))}</code>\n"
            f"Token: <code>{html.escape(str(p.get('mint') or ''))}</code>\n"
            f"Reason: <code>{html.escape(str(reason)[:700])}</code>\n"
            "Position remains OPEN; other eligible COLD ZONE trading continues when this position is confirmed stuck/rugged.",
            str(p.get("position_id") or ""),
            str(p.get("mint") or ""),
        )
    _update_cold(app, str(p.get("position_id") or ""), **fields)


def _writeoff_notice_if_due(app, p: dict, cold: dict) -> None:
    if not int(cold.get("rug_suspected") or 0):
        return
    now = _now()
    last = int(cold.get("last_writeoff_notice_at") or 0)
    if now - last < WRITE_OFF_REMINDER_SECONDS:
        return
    pid = str(p.get("position_id") or "")
    mint = str(p.get("mint") or "")
    _queue_notice(
        app,
        str(p.get("telegram_id") or ""),
        "WRITEOFF_REMINDER",
        "🚨 <b>COLD ZONE RUG / LIQUIDITY-STUCK POSITION</b>\n"
        "This position no longer consumes one of the 10 active COLD ZONE slots, so new trading is not frozen.\n"
        f"Position: <code>{html.escape(pid)}</code>\n"
        f"Token: <code>{html.escape(mint)}</code>\n"
        f"Last failure: <code>{html.escape(str(cold.get('rug_reason') or '')[:650])}</code>\n\n"
        "The bot keeps retrying an executable exit. If you accept that the remaining token is economically unrecoverable, you can close only the accounting position with:\n"
        f"<code>/solanawriteoff {html.escape(pid)} CONFIRM</code>\n\n"
        "Write-off sends NO sale transaction, leaves any remaining token in the wallet, records the remaining entry cost as realised loss, and frees the accounting position. It does not recover money or burn the token.",
        pid,
        mint,
    )
    _update_cold(app, pid, last_writeoff_notice_at=now)


def _attempt_full_exit(app, p: dict, cold: dict, reason: str) -> bool:
    now = _now()
    last = int(cold.get("last_exit_attempt_at") or 0)
    if now - last < EXIT_RETRY_SECONDS:
        return False
    _update_cold(app, str(p.get("position_id") or ""), last_exit_attempt_at=now)
    try:
        result = _live._close_live(app, str(p.get("telegram_id") or ""), p, Decimal(1), reason)
        net = _d(result.get("net_sol"), 0)
        entry_cost = max(Decimal("0.000000001"), _d(p.get("entry_cost_sol"), 0))
        pct = net * Decimal(100) / entry_cost
        usd_text = "USD unavailable"
        try:
            from . import telegram_solana_everywhere_compat_patch as _usd
            sol_usd = _usd._sol_price_usd()
            if sol_usd is not None and _d(sol_usd, 0) > 0:
                usd_text = f"${(net * _d(sol_usd, 0)):+.6f}"
        except Exception:
            pass
        _queue_notice(
            app,
            str(p.get("telegram_id") or ""),
            "SELL_CONFIRMED",
            "💚 <b>COLD ZONE SELL CONFIRMED</b>" if net > 0 else ("❤️ <b>COLD ZONE SELL CONFIRMED</b>" if net < 0 else "🍉 <b>COLD ZONE SELL CONFIRMED</b>"),
            str(p.get("position_id") or ""), str(p.get("mint") or ""),
        )
        # Queue the detail separately so the relay preserves exact true-net data.
        _queue_notice(
            app,
            str(p.get("telegram_id") or ""),
            "SELL_NET_DETAIL",
            f"Reason: <code>{html.escape(reason)}</code>\n"
            f"True realised net: <b>{net:+.9f} SOL</b>\n"
            f"True realised net: <b>{pct:+.3f}%</b>\n"
            f"True realised net USD: <b>{html.escape(usd_text)}</b>\n"
            f"TX: <code>{html.escape(str(result.get('signature') or ''))}</code>",
            str(p.get("position_id") or ""), str(p.get("mint") or ""),
        )
        return True
    except Exception as exc:
        _failure_state(app, p, cold, f"{type(exc).__name__}: {str(exc)[:700]}")
        return False


def monitor_positions_cold_zone(app):
    now = _now()
    with closing(_sol.connect(app)) as conn:
        _ensure(conn)
        rows = [dict(r) for r in conn.execute(
            """SELECT p.* FROM positions p JOIN cold_zone_positions c ON c.position_id=p.position_id
               WHERE p.status='OPEN' AND p.mode='LIVE' ORDER BY p.updated_at"""
        ).fetchall()]

    for p in rows:
        tid = str(p.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        with closing(_sol.connect(app)) as conn:
            cold = _cold_row(conn, str(p.get("position_id") or ""))
        if not cold:
            continue
        pool_age = max(0, now - int(cold.get("pool_created_at") or now))

        # At 50 minutes, the strategy has no P&L floor. We attempt the full exit
        # even when an evaluation quote is unavailable. Current transaction validity
        # and route-existence protections remain authoritative; a truly unsaleable
        # token becomes nonblocking/write-off eligible rather than freezing trading.
        if pool_age >= FORCED_EXIT_POOL_AGE_SECONDS:
            _attempt_full_exit(app, p, cold, "COLD_ZONE_50M_FORCED_EXIT_AFTER_5M_LOSS_GRACE")
            with closing(_sol.connect(app)) as conn:
                refreshed = _cold_row(conn, str(p.get("position_id") or "")) or cold
            _writeoff_notice_if_due(app, p, refreshed)
            continue

        try:
            ev = _sol.evaluate_position(app, p)
            current = _d(ev.get("net_pct"), 0)
            peak = max(_d(p.get("peak_unrealised_pct"), 0), current)
            with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
                conn.execute(
                    "UPDATE positions SET current_exit_sol=?,unrealised_net_sol=?,unrealised_pct=?,peak_unrealised_pct=?,updated_at=? WHERE position_id=?",
                    (str(ev.get("proceeds_sol") or "0"), str(ev.get("net_sol") or "0"), float(current), float(peak), now, str(p.get("position_id") or "")),
                )
                conn.commit()
            # A successful executable quote clears transient liquidity-failure history.
            if int(cold.get("rug_suspected") or 0) == 0 and int(cold.get("liquidity_failures") or 0) > 0:
                _update_cold(app, str(p.get("position_id") or ""), liquidity_failures=0, first_liquidity_failure_at=None)
        except Exception as exc:
            _failure_state(app, p, cold, f"valuation/exit quote failed: {type(exc).__name__}: {str(exc)[:650]}")
            with closing(_sol.connect(app)) as conn:
                refreshed = _cold_row(conn, str(p.get("position_id") or "")) or cold
            _writeoff_notice_if_due(app, p, refreshed)
            continue

        if current >= TARGET_NET_PCT:
            _attempt_full_exit(app, p, cold, "COLD_ZONE_NET_TARGET_5_PERCENT")
            continue

        if pool_age >= ENTRY_POOL_AGE_LIMIT_SECONDS:
            if current >= 0:
                _attempt_full_exit(app, p, cold, "COLD_ZONE_45M_ANY_NET_PROFIT")
            elif not int(cold.get("grace_notice_sent") or 0):
                _queue_notice(
                    app,
                    tid,
                    "LOSS_GRACE",
                    "⏳ <b>COLD ZONE 45-MINUTE DEADLINE</b>\n"
                    f"Current executable net: <b>{current:+.3f}%</b>\n"
                    "The position is in loss, so the requested 5-minute loss grace has started. At pool age 50 minutes the bot will attempt a full exit with no P&L floor.",
                    str(p.get("position_id") or ""), str(p.get("mint") or ""),
                )
                _update_cold(app, str(p.get("position_id") or ""), grace_notice_sent=1)

        with closing(_sol.connect(app)) as conn:
            refreshed = _cold_row(conn, str(p.get("position_id") or "")) or cold
        _writeoff_notice_if_due(app, p, refreshed)

    _sol.export_csv(app)


def install() -> None:
    if getattr(_sol, "_cold_zone_17aug_installed", False):
        return
    with _sol._DB_LOCK, closing(_sol.connect) if False else closing(_sol.connect):
        pass

    # Schema is created lazily per app connection; runtime hooks are installed here.
    _sol.settings = settings_cold_zone
    _sol.refresh_rankings = refresh_rankings_17aug
    _sol._validate_shadow_entry = _cold_preflight
    _sol.process_leader_event = process_leader_event_cold_zone
    _sol.monitor_positions = monitor_positions_cold_zone
    _sol._cold_zone_17aug_installed = True
    print(
        "[solana-cold-zone] installed=true profile=COLD_ZONE_17AUG_V1 leaders=2 min_trades=5 win_rate>=50% "
        "trade=0.0005 active_slots=10 pool_entry_age<=45m net_target=5% 45m_profit_exit=true "
        "50m_no_pnl_floor=true leader_sells=ignored partial_sells=off collapse_exit=off writeoff_notice=30m"
    )


# Avoid requiring an app at import time; install hooks now and create DB tables on first use.
# The odd-looking no-op from earlier revisions is deliberately absent here.
def _install_hooks_only() -> None:
    if getattr(_sol, "_cold_zone_17aug_installed", False):
        return
    _sol.settings = settings_cold_zone
    _sol.refresh_rankings = refresh_rankings_17aug
    _sol._validate_shadow_entry = _cold_preflight
    _sol.process_leader_event = process_leader_event_cold_zone
    _sol.monitor_positions = monitor_positions_cold_zone
    _sol._cold_zone_17aug_installed = True
    print(
        "[solana-cold-zone] installed=true profile=COLD_ZONE_17AUG_V1 leaders=2 min_trades=5 win_rate>=50% "
        "trade=0.0005 active_slots=10 pool_entry_age<=45m net_target=5% 45m_profit_exit=true "
        "50m_no_pnl_floor=true leader_sells=ignored partial_sells=off collapse_exit=off writeoff_notice=30m"
    )


_install_hooks_only()
