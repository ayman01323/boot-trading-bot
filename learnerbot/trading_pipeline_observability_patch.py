from __future__ import annotations

"""Read-only trading-funnel observability and SHADOW EVM reconstruction evidence.

This patch is deliberately observational.  It does not relax any leader-quality,
profit, liquidity, simulation, reserve, signing, LIVE/ARMED, capital or execution
gate.  Unknown EVM router/aggregator flows are reconstructed only into diagnostic
SHADOW counters; they are never inserted into wallet_trades.
"""

import asyncio
import csv
import html
import json
import os
import threading
import time
from collections import Counter, defaultdict
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import ai_four_agent_health_patch as _health5
from . import ai_health_compact_report_patch as _compact
from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_legacy_backlog_drainer_patch as _drainer
from . import sibot_wrapped_base_history_patch as _wrapped
from . import solana_leader_edge_alignment_patch as _sol_edge
from . import solana_sibot as _sol
from . import solana_trade_diagnostics_patch as _sol_diag
from . import telegram_ai_ops_patch as _ai_ops
from . import telegram_trade_blocker_health_patch as _trade_health
from . import auto_trader as _auto
from .config import AppSettings, load_chains, load_kv_scoped
from .execution_quarantine import quarantine_state, route_or_token_blocked
from .multi_wallet_store import MultiWalletStore
from .product_universe import route_product_policy
from .user_registry import all_users

_EVM_BRIDGE = Path("/var/tmp/boot/evm_reconstruction_status.json")
_MASTER_BRIDGE = Path("/var/tmp/boot/trading_funnel_master.json")
_EVM_SELECTOR_BRIDGE = Path("/var/tmp/boot/evm_leader_selector.json")
_SOL_SELECTOR_BRIDGE = Path("/var/tmp/boot/solana_leader_selector.json")
_RESEARCH_BRIDGE = Path("/var/tmp/boot/strategy_factory_leader_research.json")

_BRIDGE_LOCK = threading.Lock()
_STARTED_LOCK = threading.Lock()
_STARTED = False
_RESEARCH_RUNNING = False
_SOL_ZERO_STREAK = 0

_PREV_STORE_SUCCESS = _alchemy._store_success
_PREV_SOL_WRITE_BRIDGE = _sol_edge._write_bridge
_PREV_ENGINEERING_TEXT = _compact.engineering_text
_PREV_STRATEGY_TEXT = _compact.strategy_text


def _bool(value, default=False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _rows(path: Path) -> list[dict]:
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _recent(rows: list[dict], seconds=3600) -> list[dict]:
    cutoff = int(time.time()) - max(1, int(seconds))
    out = []
    for row in rows:
        ts = _int(row.get("timestamp_epoch") or row.get("updated_epoch") or row.get("ts"), 0)
        if ts >= cutoff:
            out.append(row)
    return out


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _age(ts: int) -> str:
    ts = _int(ts, 0)
    if not ts:
        return "unknown"
    sec = max(0, int(time.time()) - ts)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h"


def _event_counts(wallet: str, routers: set[str], normal_rows: list[dict], token_rows: list[dict], internal_rows: list[dict], chain_id: int) -> dict:
    """Count the exact direct/wrapped directional patterns used by the active reconstructor."""
    wrapped = _wrapped._WRAPPED_BASE.get(int(chain_id), "")
    w = str(wallet or "").lower()
    normals = {
        str(row.get("hash") or "").lower(): row
        for row in normal_rows
        if _wrapped._successful_normal(row) and str(row.get("from") or "").lower() == w
    }
    flows = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0}))
    for row in token_rows:
        txh = str(row.get("hash") or "").lower()
        if txh not in normals:
            continue
        token = str(row.get("contractAddress") or "").lower()
        raw = _sibot._int(row.get("value"), 0)
        if not token or raw <= 0:
            continue
        frm = str(row.get("from") or "").lower()
        to = str(row.get("to") or "").lower()
        if to == w and frm != w:
            flows[txh][token]["in"] += raw
        if frm == w and to != w:
            flows[txh][token]["out"] += raw

    internal_in = defaultdict(Decimal)
    for row in internal_rows:
        if str(row.get("isError") or "0") == "1" or str(row.get("to") or "").lower() != w:
            continue
        txh = str(row.get("hash") or "").lower()
        if txh in normals:
            internal_in[txh] += Decimal(str(_sibot._int(row.get("value"), 0))) / Decimal(10**18)

    recognised_buys = recognised_sells = shadow_buys = shadow_sells = router_txs = 0
    reasons: Counter = Counter()
    destinations: Counter = Counter()
    for txh, tx in normals.items():
        destination = str(tx.get("to") or "").lower()
        recognised = (not routers) or destination in routers
        if recognised:
            router_txs += 1
        else:
            reasons["top-level destination not recognised router"] += 1
            if destination:
                destinations[destination] += 1

        native_value = Decimal(str(_sibot._int(tx.get("value"), 0))) / Decimal(10**18)
        token_items = []
        for token, flow in flows.get(txh, {}).items():
            if token == wrapped:
                continue
            net = int(flow.get("in", 0)) - int(flow.get("out", 0))
            if net:
                token_items.append((token, net))
        positive = [x for x in token_items if x[1] > 0]
        negative = [x for x in token_items if x[1] < 0]
        wf = flows.get(txh, {}).get(wrapped) or {}
        wrapped_net = int(wf.get("in", 0)) - int(wf.get("out", 0))

        buy_like = (
            (native_value > 0 and len(positive) == 1 and not negative)
            or (native_value == 0 and wrapped_net < 0 and len(positive) == 1 and not negative)
        )
        sell_like = (
            (native_value == 0 and len(negative) == 1 and not positive and internal_in.get(txh, Decimal(0)) > 0)
            or (native_value == 0 and wrapped_net > 0 and len(negative) == 1 and not positive and internal_in.get(txh, Decimal(0)) <= 0)
        )
        if recognised:
            if buy_like:
                recognised_buys += 1
            elif sell_like:
                recognised_sells += 1
            elif len(positive) > 1 or len(negative) > 1:
                reasons["recognised router multi-token/multi-hop flow not reconstructed"] += 1
            elif not positive and not negative:
                reasons["recognised router no non-base net token flow"] += 1
            else:
                reasons["recognised router unsupported base-flow shape"] += 1
        else:
            if buy_like:
                shadow_buys += 1
            elif sell_like:
                shadow_sells += 1
            elif len(positive) > 1 or len(negative) > 1:
                reasons["unrecognised router/aggregator multi-token flow"] += 1

    return {
        "normal_txs": len(normals),
        "router_txs": router_txs,
        "buys": recognised_buys,
        "sells": recognised_sells,
        "shadow_unrecognised_buys": shadow_buys,
        "shadow_unrecognised_sells": shadow_sells,
        "rejection_counts": dict(reasons.most_common()),
        "top_unrecognised_destinations": destinations.most_common(10),
    }


def reconstruction_diagnostic(wallet: str, routers: set[str], normal_rows: list[dict], token_rows: list[dict], internal_rows: list[dict], chain_id: int, chain_slug: str) -> dict:
    counts = _event_counts(wallet, routers, normal_rows, token_rows, internal_rows, chain_id)
    current, unmatched = _sibot.reconstruct_spot_trades(
        wallet, routers, normal_rows, token_rows, internal_rows, int(chain_id), str(chain_slug)
    )
    # SHADOW ONLY: removing the top-level router allow-list here is diagnostic replay.
    # These extra matches are never written to wallet_trades and therefore cannot
    # qualify a leader or reach LIVE execution.
    shadow_all, shadow_unmatched = _sibot.reconstruct_spot_trades(
        wallet, set(), normal_rows, token_rows, internal_rows, int(chain_id), str(chain_slug)
    )
    reason = ""
    if not current:
        if counts["normal_txs"] == 0:
            reason = "no outgoing wallet transactions in fetched history"
        elif counts["router_txs"] == 0:
            reason = "no outgoing transaction targeted a recognised router"
        elif counts["buys"] == 0:
            reason = "recognised-router transactions produced no supported BUY event"
        elif counts["sells"] == 0:
            reason = "BUY events exist but no supported SELL event was reconstructed"
        else:
            reason = "BUY/SELL events exist but no closed FIFO trade matched"
    return {
        "transfer_rows": len(token_rows) + len(internal_rows),
        **counts,
        "matched_closed": len(current),
        "unmatched_sells": int(unmatched),
        "shadow_all_routes_matched_closed": len(shadow_all),
        "shadow_extra_matched_closed": max(0, len(shadow_all) - len(current)),
        "shadow_all_routes_unmatched_sells": int(shadow_unmatched),
        "diagnostic_reason": reason,
        "shadow_only": True,
    }


def _write_evm_reconstruction_bridge(chain, wallet: str, complete: bool, diagnostic: dict) -> None:
    with _BRIDGE_LOCK:
        payload = _read_json(_EVM_BRIDGE)
        if not payload:
            payload = {"schema_version": 1, "chains": {}}
        chains = payload.setdefault("chains", {})
        slug = str(chain.slug)
        chain_row = chains.setdefault(slug, {"chain_id": int(chain.chain_id), "wallets": {}})
        wallets = chain_row.setdefault("wallets", {})
        wallets[str(wallet).lower()] = {
            **diagnostic,
            "history_complete": bool(complete),
            "updated_epoch": int(time.time()),
        }
        # Bounded latest evidence only; wallet addresses are already public chain data,
        # but keep the bridge compact and avoid turning it into a historical database.
        ordered = sorted(wallets.items(), key=lambda kv: _int((kv[1] or {}).get("updated_epoch"), 0), reverse=True)[:25]
        chain_row["wallets"] = dict(ordered)
        chain_row["latest"] = ordered[0][1] if ordered else {}
        chain_row["updated_epoch"] = int(time.time())
        payload["generated_epoch"] = int(time.time())
        _atomic_json(_EVM_BRIDGE, payload)


def _store_success_with_observability(app, chain, wallet: str, fetched_at: int, normal: list[dict], token: list[dict], internal: list[dict], complete: bool):
    result = _PREV_STORE_SUCCESS(app, chain, wallet, fetched_at, normal, token, internal, complete)
    try:
        diag = reconstruction_diagnostic(
            wallet,
            {str(x).lower() for x in _sibot._routers(app, chain)},
            normal,
            token,
            internal,
            int(chain.chain_id),
            str(chain.slug),
        )
        _write_evm_reconstruction_bridge(chain, wallet, complete, diag)
        print(
            "[evm-reconstruction-funnel:%s] transfers=%d router_txs=%d buys=%d sells=%d matched_closed=%d shadow_extra=%d reason=%s"
            % (
                chain.slug,
                diag["transfer_rows"],
                diag["router_txs"],
                diag["buys"],
                diag["sells"],
                diag["matched_closed"],
                diag["shadow_extra_matched_closed"],
                diag["diagnostic_reason"] or "ok",
            ),
            flush=True,
        )
    except Exception as exc:
        print(f"[evm-reconstruction-observability] {type(exc).__name__}: {str(exc)[:220]}", flush=True)
    return result


def _classify_reason_code(reason: str) -> str:
    """Map a raw failure-reason string to the standard Solana funnel reason-code taxonomy.

    Taxonomy:
      NO_CANDIDATE        — pool was empty (no qualified leaders found)
      RPC_DATA_FAILURE    — RPC or data-fetch error prevented evaluation
      STALE_SIGNAL        — signal too old to act on
      POOL_RISK_REJECT    — pool/rug/dex risk gate blocked
      POOL_LIQUIDITY_REJECT — liquidity check failed
      ROUTE_QUOTE_FAILURE — Jupiter quote or route unavailable
      SAFETY_GATE         — simulation, drawdown, circuit-breaker or other safety gate
      CONTROL_PLANE_BLOCK — ARMED/LIVE/AUTO control plane not enabled
      SIGNER_FUNDING_BLOCK — signer not ready or insufficient SOL balance
      CAPITAL_LIMIT       — max-position or capital cap reached
      OTHER               — unclassified failure
    """
    r = str(reason or "").lower()
    if any(k in r for k in ("rpc", "transport", "json-rpc", "request", "unavailable", "metrics unavailable")):
        return "RPC_DATA_FAILURE"
    if any(k in r for k in ("stale", "too old", "signal age", "expired")):
        return "STALE_SIGNAL"
    if any(k in r for k in ("rug", "poolcheck", "hard_block", "pool risk", "dexscreener", "external_pool")):
        return "POOL_RISK_REJECT"
    if any(k in r for k in ("liquidity", "lp", "illiquid")):
        return "POOL_LIQUIDITY_REJECT"
    if any(k in r for k in ("quote", "route", "jupiter", "slippage", "price impact")):
        return "ROUTE_QUOTE_FAILURE"
    if any(k in r for k in ("simulation", "drawdown", "circuit", "circuit-breaker", "halt", "safety")):
        return "SAFETY_GATE"
    if any(k in r for k in ("armed", "live", "auto", "control", "control_plane", "not enabled", "off")):
        return "CONTROL_PLANE_BLOCK"
    if any(k in r for k in ("signer", "signing", "funding", "balance", "reserve", "insufficient sol", "wallet")):
        return "SIGNER_FUNDING_BLOCK"
    if any(k in r for k in ("max_open", "position limit", "capital", "capacity", "limit")):
        return "CAPITAL_LIMIT"
    if any(k in r for k in (
        "win rate", "profit factor", "drawdown", "net profit", "median return",
        "quality gate", "edge floor", "recent",
    )):
        return "SAFETY_GATE"
    return "OTHER"


def _build_reason_codes(pool: int, qualified: int, failures: Counter) -> dict[str, int]:
    """Aggregate raw failure-reason strings into the standard reason-code taxonomy."""
    codes: Counter = Counter()
    if int(pool) == 0:
        codes["NO_CANDIDATE"] += 1
    for reason, count in failures.items():
        code = _classify_reason_code(str(reason))
        codes[code] += int(count)
    return dict(codes)


def _sol_write_bridge_with_streak(pool: int, qualified: int, selected: int, failures: Counter, cfg: dict) -> None:
    global _SOL_ZERO_STREAK
    _PREV_SOL_WRITE_BRIDGE(pool, qualified, selected, failures, cfg)
    if int(pool) > 0 and int(qualified) == 0:
        _SOL_ZERO_STREAK += 1
    else:
        _SOL_ZERO_STREAK = 0
    reason_codes = _build_reason_codes(pool, qualified, failures)
    try:
        with _BRIDGE_LOCK:
            payload = _read_json(_SOL_SELECTOR_BRIDGE)
            payload["zero_qualified_streak"] = int(_SOL_ZERO_STREAK)
            payload["research_needed"] = bool(_SOL_ZERO_STREAK >= 3 and int(pool) > 0 and int(qualified) == 0)
            payload["research_trigger_reason"] = (
                "positive candidate pool failed all unchanged quality gates for at least three consecutive selector cycles"
                if payload["research_needed"] else ""
            )
            payload["reason_codes"] = reason_codes
            payload["funnel"] = {
                "broader_pool": int(pool),
                "qualified": int(qualified),
                "selected": int(selected),
            }
            _atomic_json(_SOL_SELECTOR_BRIDGE, payload)
        # Concise diagnostic log line for observability
        code_summary = " ".join(f"{k}={v}" for k, v in sorted(reason_codes.items())) or "none"
        print(
            f"[solana-funnel] pool={pool} qualified={qualified} selected={selected} "
            f"zero_streak={_SOL_ZERO_STREAK} codes={{{code_summary}}}",
            flush=True,
        )
    except Exception:
        pass


def _evm_summary(app) -> dict:
    selector = _read_json(_EVM_SELECTOR_BRIDGE)
    recon = _read_json(_EVM_BRIDGE)
    out = {"chains": {}, "first_zero_stage": ""}
    try:
        with closing(_sibot.connect(app)) as conn:
            for chain in load_chains(app, enabled_only=True):
                if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
                    continue
                cid = int(chain.chain_id)
                status = conn.execute(
                    """SELECT COUNT(*) n,
                              SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) complete,
                              MAX(fetched_at) newest
                       FROM wallet_history_status WHERE chain_id=?""",
                    (cid,),
                ).fetchone()
                trades = conn.execute(
                    "SELECT COUNT(*) n,COUNT(DISTINCT lower(wallet)) wallets FROM wallet_trades WHERE chain_id=?",
                    (cid,),
                ).fetchone()
                sel = ((selector.get("chains") or {}).get(str(chain.slug)) or {})
                rchain = ((recon.get("chains") or {}).get(str(chain.slug)) or {})
                latest = rchain.get("latest") or {}
                try:
                    drain = _drainer.status_for_chain(app, chain)
                except Exception:
                    drain = {}
                row = {
                    "history_wallets": int(status["n"] or 0) if status else 0,
                    "history_complete": int(status["complete"] or 0) if status else 0,
                    "history_newest": int(status["newest"] or 0) if status else 0,
                    "reconstructed_trades": int(trades["n"] or 0) if trades else 0,
                    "wallets_with_trades": int(trades["wallets"] or 0) if trades else 0,
                    "pool": _int(sel.get("pool"), 0),
                    "qualified": _int(sel.get("qualified"), 0),
                    "selected": _int(sel.get("selected"), 0),
                    "selector_failure_counts": sel.get("first_failure_counts") or {},
                    "latest_reconstruction": latest,
                    "backlog": drain,
                }
                if row["history_wallets"] == 0:
                    row["first_zero_stage"] = "history"
                elif row["reconstructed_trades"] == 0:
                    row["first_zero_stage"] = "reconstructed"
                elif row["pool"] == 0:
                    row["first_zero_stage"] = "pool"
                elif row["qualified"] == 0:
                    row["first_zero_stage"] = "qualified"
                elif row["selected"] == 0:
                    row["first_zero_stage"] = "selected"
                else:
                    row["first_zero_stage"] = "copied/awaiting leader event"
                out["chains"][str(chain.slug)] = row
        first = [f"{slug}:{row['first_zero_stage']}" for slug, row in out["chains"].items() if row.get("first_zero_stage")]
        out["first_zero_stage"] = ", ".join(first[:5])
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


def _polygon_summary(app) -> dict:
    now = int(time.time())
    status_rows = _rows(Path(app.csv_dir) / "auto" / "fast_market_status.csv")
    status = status_rows[-1] if status_rows else {}
    live_rows = [
        r for r in _rows(Path(app.csv_dir) / "live_opportunities.csv")
        if str(r.get("chain_slug") or "").lower() == "polygon" or str(r.get("chain_id") or "") == "137"
    ]
    eligible = []
    qstate = quarantine_state(app.csv_dir, 137, now)
    for row in live_rows:
        if not _auto._eligible_scanner_candidate(row):
            continue
        path = [x for x in str(row.get("route_path") or "").split(">") if x]
        blocked, _ = route_or_token_blocked(qstate, str(row.get("route_id") or ""), path)
        if blocked:
            continue
        if not route_product_policy(app.csv_dir, 137, path).get("auto_trade"):
            continue
        eligible.append(row)

    platform_auto = _bool(load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0).get("auto_trading_enabled"), False)
    platform_live = _bool(load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", 0).get("trading_enabled"), False)
    ready_users = 0
    try:
        store = MultiWalletStore(app.data_dir, app.csv_dir)
        for user in all_users(app.csv_dir, enabled_only=True):
            if str(user.get("status") or "").upper() != "ACTIVE" or not _bool(user.get("can_auto_trade"), True):
                continue
            tid = str(user.get("telegram_id") or "").strip()
            if not tid or not store.has_wallet(tid):
                continue
            cfg = _auto._user_exec_config(app, tid, 137)
            if cfg["auto_on"] and cfg["live_on"] and cfg["mode"] == "ARMED":
                ready_users += 1
    except Exception:
        ready_users = 0

    sims = [
        r for r in _recent(_rows(Path(app.csv_dir) / "auto" / "auto_trade_simulations.csv"), 3600)
        if str(r.get("chain_slug") or "").lower() == "polygon" or str(r.get("chain_id") or "") == "137"
    ]
    execs = [
        r for r in _recent(_rows(Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"), 3600)
        if str(r.get("chain_slug") or "").lower() == "polygon" or str(r.get("chain_id") or "") == "137"
    ]
    successful_sims = [r for r in sims if _bool(r.get("simulation_ok"), False)]
    submitted = len(execs)
    broadcast = sum(1 for r in execs if str(r.get("tx_hash") or "").strip())
    filled = sum(1 for r in execs if str(r.get("status") or "").upper() in {"SUCCESS", "SUCCESS_FEE_PENDING"})
    ready_routes = len(eligible) if platform_auto and platform_live and ready_users > 0 else 0

    first_reason = ""
    if len(live_rows) == 0:
        first_zero = "scanned/routes"
        first_reason = str(status.get("note") or "no Polygon routes in current live opportunity feed")[:240]
    elif len(eligible) == 0:
        first_zero = "economically eligible"
        first_reason = "current routes fail scanner/quarantine/product-policy eligibility"
    elif ready_routes == 0:
        first_zero = "AUTO/LIVE ready"
        gates = []
        if not platform_auto:
            gates.append("platform AUTO off")
        if not platform_live:
            gates.append("platform LIVE off")
        if ready_users == 0:
            gates.append("no active wallet is per-user AUTO+LIVE+ARMED ready")
        first_reason = "; ".join(gates) or "no ready route/user pair"
    elif len(sims) == 0:
        first_zero = "simulated"
        first_reason = "ready routes exist but no Polygon wallet simulation was recorded in the last hour"
    elif len(successful_sims) == 0:
        first_zero = "route found"
        reasons = Counter(str(r.get("reason") or "simulation rejected") for r in sims)
        first_reason = reasons.most_common(1)[0][0][:240] if reasons else "all simulations rejected"
    elif submitted == 0:
        first_zero = "submission attempted"
        first_reason = "simulation succeeded but no execution submission row was recorded in the last hour"
    elif broadcast == 0:
        first_zero = "broadcast"
        reasons = Counter(str(r.get("note") or "submission rejected before broadcast") for r in execs)
        first_reason = reasons.most_common(1)[0][0][:240] if reasons else "submission did not produce a transaction hash"
    elif filled == 0:
        first_zero = "filled"
        first_reason = "broadcast/execution rows exist but none is receipt-confirmed SUCCESS in the last hour"
    else:
        first_zero = "none"
        first_reason = "at least one Polygon AUTO trade filled in the last hour"

    return {
        "scanned_routes": len(live_rows),
        "economically_eligible": len(eligible),
        "ready_users": ready_users,
        "auto_live_ready": ready_routes,
        "simulated": len(sims),
        "route_found": len(successful_sims),
        "submission_attempted": submitted,
        "broadcast": broadcast,
        "filled": filled,
        "platform_auto": platform_auto,
        "platform_live": platform_live,
        "scanner_status": str(status.get("status") or "UNKNOWN"),
        "scanner_updated_epoch": _int(status.get("updated_epoch"), 0),
        "first_zero_stage": first_zero,
        "first_rejection_reason": first_reason,
    }


def _solana_summary(app, tid) -> dict:
    bridge = _read_json(_SOL_SELECTOR_BRIDGE)
    try:
        activity = _sol_diag.activity_summary(app, str(tid), 24)
    except Exception:
        activity = {"counts": {}, "leaders": 0, "events": 0}
    discovered = reconstructed = 0
    try:
        with closing(_sol.connect(app)) as conn:
            try:
                discovered = int(conn.execute("SELECT COUNT(DISTINCT wallet) n FROM history_status").fetchone()["n"] or 0)
            except Exception:
                discovered = 0
            try:
                reconstructed = int(conn.execute("SELECT COUNT(DISTINCT wallet) n FROM trades").fetchone()["n"] or 0)
            except Exception:
                reconstructed = 0
    except Exception:
        pass
    counts = activity.get("counts") or {}
    preflight = sum(_int(v, 0) for v in counts.values())
    copied = _int(counts.get("BUY"), 0)
    pool = _int(bridge.get("pool"), 0)
    qualified = _int(bridge.get("qualified"), 0)
    selected = _int(bridge.get("selected"), 0)
    if discovered == 0:
        first_zero = "discovered"
    elif reconstructed == 0:
        first_zero = "reconstructed"
    elif pool == 0:
        first_zero = "positive pool"
    elif qualified == 0:
        first_zero = "quality-qualified"
    elif selected == 0:
        first_zero = "selected"
    elif preflight == 0:
        first_zero = "preflight"
    elif copied == 0:
        first_zero = "copied"
    else:
        first_zero = "none"
    return {
        "discovered": discovered,
        "reconstructed": reconstructed,
        "positive_pool": pool,
        "quality_qualified": qualified,
        "selected": selected,
        "preflight_decisions_24h": preflight,
        "copied_buys_24h": copied,
        "first_failure_counts": bridge.get("first_failure_counts") or {},
        "zero_qualified_streak": _int(bridge.get("zero_qualified_streak"), 0),
        "research_needed": bool(bridge.get("research_needed")),
        "first_zero_stage": first_zero,
    }


def snapshot(app, tid="") -> dict:
    value = {
        "schema_version": 1,
        "generated_epoch": int(time.time()),
        "evm_sibot": _evm_summary(app),
        "polygon_auto": _polygon_summary(app),
        "solana": _solana_summary(app, tid),
        "safety_gates_unchanged": True,
        "shadow_reconstruction_only": True,
    }
    return value


def _compact_backlog_line(app) -> str:
    parts = []
    for chain in load_chains(app, enabled_only=True):
        if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
            continue
        try:
            d = _drainer.status_for_chain(app, chain)
        except Exception:
            continue
        remaining = _int(d.get("legacy_backlog"), 0) + _int(d.get("progress_backlog"), 0) + _int(d.get("transient_backlog"), 0)
        parts.append(
            f"{str(chain.slug).upper()} a{_int(d.get('attempts'))}/s{_int(d.get('successes'))}/p{_int(d.get('progress_yields'))}/f{_int(d.get('failures'))}/rl{_int(d.get('rate_limits'))} rem{remaining}"
        )
    return " • ".join(parts[:5]) or "collecting"


def build_report(app, tid) -> str:
    s = snapshot(app, tid)
    evm = s["evm_sibot"]
    poly = s["polygon_auto"]
    sol = s["solana"]
    lines = ["<b>🧭 MASTER TRADING DIAGNOSTIC</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    lines.append("<b>EVM SiBot: history → reconstructed → pool → qualified → selected → copied</b>")
    for slug, row in (evm.get("chains") or {}).items():
        latest = row.get("latest_reconstruction") or {}
        lines.append(
            f"{html.escape(slug.upper())}: history <b>{row.get('history_complete',0)}/{row.get('history_wallets',0)}</b> → "
            f"reconstructed <b>{row.get('reconstructed_trades',0)}</b> → pool <b>{row.get('pool',0)}</b> → "
            f"qualified <b>{row.get('qualified',0)}</b> → selected <b>{row.get('selected',0)}</b>"
        )
        lines.append(f"   First zero: <b>{html.escape(str(row.get('first_zero_stage') or 'unknown'))}</b>")
        if latest:
            lines.append(
                "   Latest reconstruction: "
                f"transfers {latest.get('transfer_rows',0)} → router txs {latest.get('router_txs',0)} → "
                f"BUY {latest.get('buys',0)} → SELL {latest.get('sells',0)} → matched <b>{latest.get('matched_closed',0)}</b>"
            )
            if latest.get("diagnostic_reason"):
                lines.append(f"   Reason: <code>{html.escape(str(latest.get('diagnostic_reason'))[:180])}</code>")
            if _int(latest.get("shadow_extra_matched_closed"), 0) > 0:
                lines.append(
                    f"   🟡 SHADOW router/aggregator coverage would add <b>{latest.get('shadow_extra_matched_closed')}</b> closed matches; not promoted to LIVE history."
                )
    lines += ["", "<b>EVM backlog drainer</b>", html.escape(_compact_backlog_line(app))]

    lines += ["", "<b>Polygon AUTO: scanned → eligible → AUTO/LIVE ready → simulated → route found → submitted → broadcast → filled</b>"]
    lines.append(
        f"<b>{poly['scanned_routes']}</b> → <b>{poly['economically_eligible']}</b> → <b>{poly['auto_live_ready']}</b> → "
        f"<b>{poly['simulated']}</b> → <b>{poly['route_found']}</b> → <b>{poly['submission_attempted']}</b> → "
        f"<b>{poly['broadcast']}</b> → <b>{poly['filled']}</b>"
    )
    lines.append(f"First zero: <b>{html.escape(poly['first_zero_stage'])}</b>")
    if poly.get("first_rejection_reason"):
        lines.append(f"Reason: <code>{html.escape(str(poly['first_rejection_reason'])[:220])}</code>")

    lines += ["", "<b>Solana: discovered → reconstructed → positive pool → quality-qualified → selected → preflight → copied</b>"]
    lines.append(
        f"<b>{sol['discovered']}</b> → <b>{sol['reconstructed']}</b> → <b>{sol['positive_pool']}</b> → "
        f"<b>{sol['quality_qualified']}</b> → <b>{sol['selected']}</b> → <b>{sol['preflight_decisions_24h']}</b> → <b>{sol['copied_buys_24h']}</b>"
    )
    lines.append(f"First zero: <b>{html.escape(sol['first_zero_stage'])}</b>")
    failures = sol.get("first_failure_counts") or {}
    if failures:
        lines.append("Quality failures: " + " • ".join(f"{html.escape(str(k))} {int(v)}" for k, v in sorted(failures.items(), key=lambda kv: kv[1], reverse=True)[:5]))
    if sol.get("research_needed"):
        lines.append(f"🔎 Strategy Factory leader-source research trigger: <b>ACTIVE</b> after {sol.get('zero_qualified_streak',0)} all-fail cycles.")

    lines += [
        "",
        "<i>Observability only. Existing profit, quality, liquidity, simulation, reserve, loss-quarantine, signing, LIVE/ARMED and capital gates are unchanged.</i>",
    ]
    return "\n".join(lines)


def _monitor_text(app) -> str:
    try:
        s = snapshot(app, "")
    except Exception as exc:
        return f"<b>📈 TRADING FUNNELS</b>\nTelemetry unavailable: <code>{html.escape(type(exc).__name__)}</code>"
    p = s["polygon_auto"]
    sol = s["solana"]
    evm = s["evm_sibot"]
    zero_evm = ", ".join(f"{k.upper()}={v.get('first_zero_stage')}" for k, v in (evm.get("chains") or {}).items())
    return (
        "<b>📈 TRADING FUNNELS</b>\n"
        f"EVM first zero: <b>{html.escape(zero_evm[:300] or 'collecting')}</b>\n"
        f"Polygon first zero: <b>{html.escape(str(p.get('first_zero_stage') or 'collecting'))}</b>\n"
        f"Solana first zero: <b>{html.escape(str(sol.get('first_zero_stage') or 'collecting'))}</b>"
    )


def engineering_text(state=None) -> str:
    try:
        app = AppSettings.load()
        extra = _monitor_text(app) + "\nBacklog: " + html.escape(_compact_backlog_line(app))
    except Exception as exc:
        extra = f"<b>📈 TRADING FUNNELS</b>\nTelemetry unavailable: <code>{html.escape(type(exc).__name__)}</code>"
    return _PREV_ENGINEERING_TEXT(state) + "\n\n" + extra


def strategy_text(state=None) -> str:
    try:
        app = AppSettings.load()
        s = snapshot(app, "")
        sol = s["solana"]
        extra = _monitor_text(app)
        if sol.get("first_failure_counts"):
            failures = " • ".join(
                f"{html.escape(str(k))} {int(v)}"
                for k, v in sorted(sol["first_failure_counts"].items(), key=lambda kv: kv[1], reverse=True)[:5]
            )
            extra += "\nSolana gate failures: " + failures
        if sol.get("research_needed"):
            extra += f"\n🔎 Leader-source research: <b>TRIGGERED</b> ({sol.get('zero_qualified_streak',0)} all-fail cycles)"
    except Exception as exc:
        extra = f"<b>📈 TRADING FUNNELS</b>\nTelemetry unavailable: <code>{html.escape(type(exc).__name__)}</code>"
    return _PREV_STRATEGY_TEXT(state) + "\n\n" + extra


def _publish_startup_health(app) -> None:
    _start_background(app)
    masters = [
        str(u.get("telegram_id") or "")
        for u in all_users(app.csv_dir, enabled_only=True)
        if str(u.get("role") or "").upper() == "MASTER" and str(u.get("telegram_id") or "")
    ]
    tid = masters[0] if masters else ""
    safe = snapshot(app, tid)
    try:
        _atomic_json(_MASTER_BRIDGE, safe)
    except Exception:
        pass
    try:
        path = Path(app.data_dir) / "trade_blocker_health.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(safe, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    except Exception:
        pass
    # Preserve the existing explicit platform-gate warning, but remove the stale
    # claim that a missing Etherscan key blocks Alchemy-backed EVM reconstruction.
    try:
        _trade_health._maybe_alert_platform_gate_off(
            app,
            masters,
            {
                "platform_auto": safe.get("polygon_auto", {}).get("platform_auto"),
                "platform_live": safe.get("polygon_auto", {}).get("platform_live"),
            },
        )
    except Exception:
        pass
    print("[trading-funnel-observability] master_bridge=ready etherscan_dependency_claim=removed", flush=True)


def _one_shot_evm_probe(app) -> None:
    """Use one known profitable BSC/Arbitrum wallet and publish full read-only funnel evidence."""
    time.sleep(12)
    try:
        from .multichain import contexts, close_contexts
        ctxs = contexts(app, enabled_only=True, with_rpc=False)
        try:
            evidence = []
            for ctx in ctxs:
                cid = int(ctx.config.chain_id)
                if cid not in {56, 42161}:
                    continue
                try:
                    rows = ctx.conn.execute(
                        """SELECT wallet,
                                  SUM(CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN COALESCE(net_base,0) ELSE 0 END) net,
                                  COUNT(*) n
                           FROM profit_evidence GROUP BY wallet
                           HAVING SUM(CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN COALESCE(net_base,0) ELSE 0 END) > 0
                           ORDER BY net DESC,n DESC LIMIT 3"""
                    ).fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    evidence.append((float(row["net"] or 0), cid, ctx.config.slug, str(row["wallet"] or "").lower()))
            if not evidence:
                _atomic_json(Path("/var/tmp/boot/evm_router_reconstruction_probe.json"), {"status": "NO_PROFITABLE_BSC_OR_ARBITRUM_WALLET", "generated_epoch": int(time.time())})
                return
            evidence.sort(reverse=True)
            _net, cid, slug, wallet = evidence[0]
            chain = next(c for c in load_chains(app, enabled_only=True) if int(c.chain_id) == cid)
            url = _alchemy.alchemy_rpc_url(app, cid)
            if not url:
                _atomic_json(Path("/var/tmp/boot/evm_router_reconstruction_probe.json"), {"status": "ALCHEMY_ENDPOINT_MISSING", "chain": slug, "generated_epoch": int(time.time())})
                return
            cfg = _sibot.platform_settings(app, cid)
            fetch_days = max(30, min(3650, _sibot._int(cfg.get("history_fetch_days"), 365)))
            cutoff = int(time.time()) - fetch_days * 86400
            max_pages = max(1, min(40, _sibot._int(cfg.get("history_max_pages"), 3)))
            page_size = max(100, min(1000, _sibot._int(cfg.get("history_page_size"), 1000)))
            delay = max(0.0, min(2.0, _sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))
            outbound, c_out = _alchemy._asset_pages(url, wallet, "fromAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay)
            inbound, c_in = _alchemy._asset_pages(url, wallet, "toAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay)
            transfers = _alchemy._dedupe(outbound + inbound)
            normal, outgoing_hashes, ts_by_hash = _alchemy._tx_context(url, transfers, wallet)
            token, _ = _alchemy._normalised_transfer_rows(transfers)
            try:
                internals, c_internal = _alchemy._asset_pages(url, wallet, "toAddress", ["internal"], cutoff, max_pages, page_size, delay)
                _, internal = _alchemy._normalised_transfer_rows(_alchemy._dedupe(internals))
            except Exception:
                internal = _alchemy._trace_internal(url, wallet, outgoing_hashes, ts_by_hash)
                c_internal = True
            diag = reconstruction_diagnostic(
                wallet,
                {str(x).lower() for x in _sibot._routers(app, chain)},
                normal,
                token,
                internal,
                cid,
                slug,
            )
            diag.update({
                "status": "OK",
                "chain_id": cid,
                "chain_slug": slug,
                "wallet": wallet,
                "raw_alchemy_transfer_rows": len(transfers),
                "coverage_complete": bool(c_out and c_in and c_internal),
                "generated_epoch": int(time.time()),
                "safety_gates_unchanged": True,
            })
            _atomic_json(Path("/var/tmp/boot/evm_router_reconstruction_probe.json"), diag)
            print(
                "[evm-router-probe] chain=%s raw=%d router_txs=%d buys=%d sells=%d matched=%d shadow_extra=%d"
                % (slug, len(transfers), diag["router_txs"], diag["buys"], diag["sells"], diag["matched_closed"], diag["shadow_extra_matched_closed"]),
                flush=True,
            )
        finally:
            close_contexts(ctxs)
    except Exception as exc:
        try:
            _atomic_json(Path("/var/tmp/boot/evm_router_reconstruction_probe.json"), {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:300]}", "generated_epoch": int(time.time())})
        except Exception:
            pass
        print(f"[evm-router-probe] ERROR {type(exc).__name__}: {str(exc)[:220]}", flush=True)


def _research_state_path(app) -> Path:
    return Path(app.data_dir) / "strategy_factory_leader_research_state.json"


def _maybe_start_strategy_research(app, snap: dict) -> None:
    global _RESEARCH_RUNNING
    sol = snap.get("solana") or {}
    if not sol.get("research_needed") or _RESEARCH_RUNNING:
        return
    state = _read_json(_research_state_path(app))
    if int(time.time()) - _int(state.get("last_run_epoch"), 0) < 12 * 3600:
        return
    _RESEARCH_RUNNING = True

    def worker():
        global _RESEARCH_RUNNING
        try:
            from scripts.strategy_factory_transport import exchange
            targets = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")
            failures = sol.get("first_failure_counts") or {}
            prompt = (
                "Strategy Factory research task. Solana currently has a positive reconstructed leader pool but zero candidates pass the unchanged quality/edge gates for at least three consecutive selector cycles. "
                f"Current first-gate failure counts: {json.dumps(failures, sort_keys=True)}. "
                "Research current, publicly verifiable Solana leader-source feeds, wallet-discovery methods, DEX/analytics data sources and tools that can broaden candidate discovery without weakening any quality, profit, liquidity, simulation, reserve, capital, signing or LIVE gate. Use current online research if available and cite source URLs. "
                "Return evidence, cost/rate-limit considerations, integration risks and SHADOW-only experiments. Do not edit code, deploy, trade, change settings or recommend lowering safety thresholds merely to create trades."
            )

            async def run_all():
                async def ask(target):
                    try:
                        res = await exchange(
                            "strategy-factory",
                            target,
                            prompt,
                            message_id=f"solana-leader-source-research-{int(time.time())}-{target}",
                            thread_id="solana-leader-source-research",
                            subject="Research new Solana leader sources",
                            timeout=210,
                        )
                        return target, {
                            "status": str(res.get("status") or ""),
                            "body": str(res.get("body") or "")[:5000],
                            "error": str(res.get("error") or "")[:500],
                        }
                    except Exception as exc:
                        return target, {"status": "FAILED", "body": "", "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
                return await asyncio.gather(*(ask(t) for t in targets))

            results = dict(asyncio.run(run_all()))
            payload = {
                "schema_version": 1,
                "generated_epoch": int(time.time()),
                "trigger": "solana_zero_qualified_streak",
                "zero_qualified_streak": _int(sol.get("zero_qualified_streak"), 0),
                "quality_failure_counts": failures,
                "agents": results,
                "shadow_research_only": True,
                "no_live_changes": True,
            }
            _atomic_json(_RESEARCH_BRIDGE, payload)
            _atomic_json(_research_state_path(app), {"last_run_epoch": int(time.time()), "last_status": "COMPLETED"})
            print("[strategy-factory-leader-research] completed agents=%d" % len(results), flush=True)
        except Exception as exc:
            try:
                _atomic_json(_research_state_path(app), {"last_run_epoch": int(time.time()), "last_status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            except Exception:
                pass
            print(f"[strategy-factory-leader-research] ERROR {type(exc).__name__}: {str(exc)[:220]}", flush=True)
        finally:
            _RESEARCH_RUNNING = False

    threading.Thread(target=worker, name="strategy-factory-leader-source-research", daemon=True).start()


def _monitor_loop(app) -> None:
    time.sleep(5)
    while True:
        try:
            current = AppSettings.load()
            masters = [
                str(u.get("telegram_id") or "")
                for u in all_users(current.csv_dir, enabled_only=True)
                if str(u.get("role") or "").upper() == "MASTER" and str(u.get("telegram_id") or "")
            ]
            snap = snapshot(current, masters[0] if masters else "")
            _atomic_json(_MASTER_BRIDGE, snap)
            _maybe_start_strategy_research(current, snap)
        except Exception as exc:
            print(f"[trading-funnel-monitor] {type(exc).__name__}: {str(exc)[:180]}", flush=True)
        time.sleep(60)


def _start_background(app) -> None:
    global _STARTED
    with _STARTED_LOCK:
        if _STARTED:
            return
        _STARTED = True
        threading.Thread(target=_monitor_loop, args=(app,), name="trading-funnel-monitor", daemon=True).start()
        threading.Thread(target=_one_shot_evm_probe, args=(app,), name="evm-router-reconstruction-probe", daemon=True).start()


def install() -> None:
    if getattr(_trade_health, "_trading_pipeline_observability_installed", False):
        return

    # EVM history: preserve the authoritative store function and add diagnostics only
    # after it has completed its existing wallet_trades/status update.
    _alchemy._store_success = _store_success_with_observability

    # Solana selector: preserve the selector's unchanged gates; add only a consecutive
    # all-fail counter and research-needed marker to its existing public bridge.
    _sol_edge._write_bridge = _sol_write_bridge_with_streak

    # MASTER /whynotrade and startup health now use the exact three-funnel model.
    _trade_health.build_report = build_report
    _trade_health._publish_startup_health = _publish_startup_health

    # Surface the same first-zero and backlog evidence in Engineering/Strategy Monitor.
    _compact.engineering_text = engineering_text
    _compact.strategy_text = strategy_text
    _ai_ops._engineering_text = engineering_text
    _ai_ops._strategy_text = strategy_text
    _health5._engineering_text = engineering_text
    _health5._strategy_text = strategy_text

    _trade_health._trading_pipeline_observability_installed = True
    print(
        "[trading-pipeline-observability] evm_shadow_only=true polygon_read_only=true "
        "solana_failure_counts=true strategy_factory_research_trigger=true safety_gates=unchanged",
        flush=True,
    )


install()
