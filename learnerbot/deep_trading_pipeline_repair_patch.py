from __future__ import annotations

"""Consolidated repair for the current no-trade bottlenecks.

This module is deliberately loaded late, after the audited execution hooks have
composed.  It changes discovery/research/provider scheduling only:

* EVM history: stop hammering an Alchemy account that is returning 429s.  A short
  circuit breaker sends history reconstruction to the already-configured
  Etherscan V2 account API when available.  Etherscan rows are accepted as
  complete only when none of the three 10k result windows is saturated and the
  reconstructed FIFO has no unmatched sells.
* GPT/Base discovery: patch the *active* fast_market binding as well as the source
  module binding, so the candidate-rotation fix cannot be bypassed by an earlier
  ``from ... import scan_full_power_hot_routes``.  Base receives a larger share of
  the existing quote budget; under provider pressure total calls are reduced while
  Base keeps priority.  No profit/edge threshold is lowered.
* Solana leader selection: replace the single rigid sample-size/quality gate with
  two evidence tiers.  Mature histories get moderately flexible statistical
  floors; small samples are admitted only when their evidence is substantially
  stronger.  Positive net performance, complete history and the existing LIVE
  median-return floors remain mandatory.

Nothing here enables LIVE/AUTO/ARMED, attaches a signer, broadcasts a transaction,
changes trade size, bypasses PoolCheck, weakens sellability/reverse/stress/simulation
checks, or converts a non-positive route into an executable candidate.
"""

import json
import threading
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import fast_market as _fast
from . import full_power_candidate_rotation_patch as _rotation
from . import full_power_scanner as _fp
from . import sibot as _sibot
from . import sibot_alchemy_retry_queue_patch as _retry
from . import sibot_alchemy_trace_progress_patch as _trace
from . import sibot_legacy_backlog_drainer_patch as _drainer
from . import solana_leader_edge_alignment_patch as _leader
from . import solana_sibot as _sol
from .etherscan import EtherscanV2

# ---------------------------------------------------------------------------
# EVM HISTORY PROVIDER CIRCUIT BREAKER + ETHERSCAN FALLBACK
# ---------------------------------------------------------------------------

_HISTORY_LOCK = threading.Lock()
_ETHERSCAN_LOCK = threading.Lock()
_ALCHEMY_COOLDOWN_UNTIL = 0.0
_ALCHEMY_COOLDOWN_SECONDS = 10 * 60
_ORIGINAL_NONTRACE_REFRESH = _trace._PREV_REFRESH_WALLET_HISTORY
_ORIGINAL_PROGRESSIVE_REFRESH = _trace._refresh_progressive


def _provider_pressure(result) -> bool:
    if not isinstance(result, dict):
        return False
    text = str(result.get("error") or "").lower()
    return any(
        marker in text
        for marker in (
            "http 429",
            "rpc 429",
            "rate limit",
            "compute units per second",
            "retries exhausted",
            "too many requests",
        )
    )


def _mark_alchemy_pressure() -> None:
    global _ALCHEMY_COOLDOWN_UNTIL
    with _HISTORY_LOCK:
        _ALCHEMY_COOLDOWN_UNTIL = max(
            _ALCHEMY_COOLDOWN_UNTIL,
            time.monotonic() + _ALCHEMY_COOLDOWN_SECONDS,
        )


def _alchemy_cooldown_active() -> bool:
    with _HISTORY_LOCK:
        return time.monotonic() < _ALCHEMY_COOLDOWN_UNTIL


def _payload_rows(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("result")
    return list(rows) if isinstance(rows, list) else []


def _store_etherscan_success(app, chain, wallet: str, fetched_at: int, normal, token, internal, complete: bool):
    trades, unmatched = _sibot.reconstruct_spot_trades(
        wallet,
        _sibot._routers(app, chain),
        normal,
        token,
        internal,
        chain.chain_id,
        chain.slug,
    )
    for row in trades:
        row["source"] = "ETHERSCAN_V2_FALLBACK_FIFO"

    timestamps = [
        _sibot._int(row.get("timeStamp"), 0)
        for row in list(normal) + list(token) + list(internal)
        if _sibot._int(row.get("timeStamp"), 0)
    ]
    coverage_start = min(timestamps) if timestamps else fetched_at
    coverage_end = max(timestamps) if timestamps else fetched_at
    complete = bool(complete and unmatched == 0)

    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute(
            "DELETE FROM wallet_trades WHERE chain_id=? AND wallet=?",
            (chain.chain_id, wallet.lower()),
        )
        for row in trades:
            conn.execute(
                """INSERT INTO wallet_trades(
                       trade_id,chain_id,chain_slug,wallet,token,symbol,decimals,
                       buy_tx,sell_tx,buy_ts,sell_ts,token_amount_raw,cost_native,
                       proceeds_native,buy_gas_native,sell_gas_native,net_native,
                       source,updated_at
                   ) VALUES(
                       :trade_id,:chain_id,:chain_slug,:wallet,:token,:symbol,:decimals,
                       :buy_tx,:sell_tx,:buy_ts,:sell_ts,:token_amount_raw,:cost_native,
                       :proceeds_native,:buy_gas_native,:sell_gas_native,:net_native,
                       :source,:updated_at
                   )""",
                row,
            )
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,coverage_start_ts,
                   coverage_end_ts,history_complete,unmatched_sells,normal_rows,
                   token_rows,internal_rows,error
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chain_id,wallet) DO UPDATE SET
                   chain_slug=excluded.chain_slug,
                   fetched_at=excluded.fetched_at,
                   coverage_start_ts=excluded.coverage_start_ts,
                   coverage_end_ts=excluded.coverage_end_ts,
                   history_complete=excluded.history_complete,
                   unmatched_sells=excluded.unmatched_sells,
                   normal_rows=excluded.normal_rows,
                   token_rows=excluded.token_rows,
                   internal_rows=excluded.internal_rows,
                   error=excluded.error""",
            (
                chain.chain_id,
                chain.slug,
                wallet.lower(),
                fetched_at,
                coverage_start,
                coverage_end,
                1 if complete else 0,
                unmatched,
                len(normal),
                len(token),
                len(internal),
                "",
            ),
        )
        conn.commit()

    return {
        "wallet": wallet,
        "trades": len(trades),
        "complete": complete,
        "unmatched_sells": unmatched,
        "provider": "ETHERSCAN_V2_FALLBACK",
    }


def _etherscan_fallback(app, chain, wallet: str) -> dict:
    api_key = str(getattr(app, "etherscan_api_key", "") or "").strip()
    if not api_key:
        return {
            "wallet": wallet,
            "trades": 0,
            "complete": False,
            "provider": "ETHERSCAN_V2_FALLBACK",
            "error": "Etherscan fallback unavailable: API key missing",
        }

    try:
        with _ETHERSCAN_LOCK:
            api = EtherscanV2(api_key, chain_id=int(chain.chain_id), timeout=30)
            normal = _payload_rows(api.normal(wallet))
            time.sleep(0.22)
            token = _payload_rows(api.token_transfers(wallet))
            time.sleep(0.22)
            internal = _payload_rows(api.internal(wallet))
        # EtherscanV2 currently requests one 10k page.  Exactly filling that page
        # means coverage may be truncated, so the row remains incomplete/fail-closed.
        complete = all(len(rows) < 10000 for rows in (normal, token, internal))
        return _store_etherscan_success(
            app,
            chain,
            wallet,
            int(time.time()),
            normal,
            token,
            internal,
            complete,
        )
    except Exception as exc:
        return {
            "wallet": wallet,
            "trades": 0,
            "complete": False,
            "provider": "ETHERSCAN_V2_FALLBACK",
            "error": f"EtherscanFallbackError: {type(exc).__name__}: {str(exc)[:280]}",
        }


def _history_with_fallback(original, app, chain, wallet: str):
    # While the account-wide Alchemy circuit is open, avoid wasting another costly
    # attempt if the independent Etherscan history provider is available.
    if _alchemy_cooldown_active() and str(getattr(app, "etherscan_api_key", "") or "").strip():
        fallback = _etherscan_fallback(app, chain, wallet)
        if not fallback.get("error"):
            return fallback

    result = original(app, chain, wallet)
    if not _provider_pressure(result):
        return result

    _mark_alchemy_pressure()
    if not str(getattr(app, "etherscan_api_key", "") or "").strip():
        return result

    fallback = _etherscan_fallback(app, chain, wallet)
    if not fallback.get("error"):
        return fallback
    # Keep the Alchemy pressure result authoritative when both providers fail, so
    # the existing retry/backoff code continues to back off rather than tight-loop.
    return result


def _nontrace_history(app, chain, wallet: str):
    return _history_with_fallback(_ORIGINAL_NONTRACE_REFRESH, app, chain, wallet)


def _progressive_history(app, chain, wallet: str):
    return _history_with_fallback(_ORIGINAL_PROGRESSIVE_REFRESH, app, chain, wallet)


# ---------------------------------------------------------------------------
# SOLANA ADAPTIVE EVIDENCE QUALITY
# ---------------------------------------------------------------------------

_ORIGINAL_WRITE_BRIDGE = _leader._write_bridge


def _profile(cfg: dict) -> dict[str, Decimal | int]:
    # Mature sample: moderately flexible, still positive/complete and still subject
    # to the unchanged LIVE median-return floors in leader_edge.historical_ok().
    return {
        "early_min_closed": max(3, _sol._int(cfg.get("flex_early_min_closed_trades"), 5)),
        "mature_min_closed": max(5, _sol._int(cfg.get("flex_mature_min_closed_trades"), 10)),
        "mature_win": _sol._dec(cfg.get("flex_mature_min_win_rate_pct"), 58),
        "mature_pf": _sol._dec(cfg.get("flex_mature_min_profit_factor"), "1.40"),
        "mature_dd": _sol._dec(cfg.get("flex_mature_max_drawdown_pct"), 25),
        "mature_recent_win": _sol._dec(cfg.get("flex_mature_min_recent_win_rate_pct"), 55),
        "mature_recent_pf": _sol._dec(cfg.get("flex_mature_min_recent_profit_factor"), "1.25"),
        # Small sample: allowed only when the evidence is materially stronger.
        "early_win": _sol._dec(cfg.get("flex_early_min_win_rate_pct"), 70),
        "early_pf": _sol._dec(cfg.get("flex_early_min_profit_factor"), "1.80"),
        "early_dd": _sol._dec(cfg.get("flex_early_max_drawdown_pct"), 15),
        "early_recent_win": _sol._dec(cfg.get("flex_early_min_recent_win_rate_pct"), 65),
        "early_recent_pf": _sol._dec(cfg.get("flex_early_min_recent_profit_factor"), "1.50"),
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


def _write_bridge_with_profile(pool: int, qualified: int, selected: int, failures, cfg: dict) -> None:
    _ORIGINAL_WRITE_BRIDGE(pool, qualified, selected, failures, cfg)
    try:
        p = _profile(cfg)
        with _leader._BRIDGE_LOCK:
            payload = json.loads(_leader._BRIDGE.read_text(encoding="utf-8"))
            payload["quality_mode"] = "adaptive_two_tier"
            payload["adaptive_profile"] = {k: str(v) for k, v in p.items()}
            payload["median_live_floors_unchanged"] = True
            payload["poolcheck_execution_safety_unchanged"] = True
            payload["thresholds_unchanged"] = False
            tmp = _leader._BRIDGE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(_leader._BRIDGE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GPT / BASE PRESSURE-AWARE WEIGHTED SCAN
# ---------------------------------------------------------------------------


def _raw_provider_pressure(app) -> int:
    rows = _fp._rows(Path(app.csv_dir) / "auto" / "full_power_rejections.csv")[-80:]
    count = 0
    for row in rows:
        text = str(row.get("reason") or "").lower()
        if any(marker in text for marker in ("429", "rate limit", "too many requests", "compute units per second")):
            count += 1
    return count


def _weighted_budgets(ctxs, total: int, *, base_weight: int) -> dict[str, int]:
    items = list(ctxs)
    if not items:
        return {}
    total = max(len(items), int(total))
    weights = {}
    for ctx in items:
        slug = str(getattr(ctx.config, "slug", "")).strip().lower()
        weights[slug] = max(weights.get(slug, 0), base_weight if slug == "base" else 1)
    denom = max(1, sum(weights.values()))
    budgets = {slug: max(1, total * weight // denom) for slug, weight in weights.items()}
    used = sum(budgets.values())
    order = sorted(weights, key=lambda slug: (slug != "base", slug))
    idx = 0
    while used < total:
        slug = order[idx % len(order)]
        budgets[slug] += 1
        used += 1
        idx += 1
    while used > total:
        changed = False
        for slug in reversed(order):
            if budgets[slug] > 1 and used > total:
                budgets[slug] -= 1
                used -= 1
                changed = True
        if not changed:
            break
    return budgets


def _weighted_scan_full_power_hot_routes(app, contexts):
    settings = _fp.load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    out = Path(app.csv_dir) / "auto" / "full_power_opportunities.csv"
    base_out = Path(app.csv_dir) / "auto" / "base_full_power_opportunities.csv"
    rej_path = Path(app.csv_dir) / "auto" / "full_power_rejections.csv"
    if not _fp._bool(settings.get("full_power_enabled", "true"), True):
        return out, [], []

    configured_checks = max(10, min(500, _fp._int(settings.get("fast_market_max_candidate_checks", "60"), 60)))
    max_routes = max(1, min(100, _fp._int(settings.get("fast_market_max_routes_per_pass", "20"), 20)))
    ctxs = list(contexts)
    pressure = _raw_provider_pressure(app)

    # Under pressure, do *fewer* total calls and direct a larger share to Base.
    # This is intentionally the opposite of the earlier idea of doubling quote
    # volume, which would worsen an account-level 429 condition.
    if pressure >= 3:
        effective_checks = max(20, int(configured_checks * 0.70))
        base_weight = 5
        worker_cap = 3
    else:
        effective_checks = configured_checks
        base_weight = 4
        worker_cap = 5

    check_budgets = _weighted_budgets(ctxs, effective_checks, base_weight=base_weight)
    route_budgets = _weighted_budgets(ctxs, max_routes, base_weight=max(2, base_weight - 1))
    rows = []
    rejected = []

    def scan_one(ctx):
        slug = str(getattr(ctx.config, "slug", "")).strip().lower()
        checks = max(3, int(check_budgets.get(slug, 3)))
        route_budget = max(1, int(route_budgets.get(slug, 1)))
        v2_budget = max(1, int(checks * 0.35))
        v3_budget = max(1, int(checks * 0.50))
        cross_budget = max(1, checks - v2_budget - v3_budget)
        r0, e0 = _fp._scan_v2_hot_chain(app, ctx, settings, v2_budget, route_budget)
        r1, e1 = _fp._scan_v3_chain(app, ctx, settings, v3_budget, route_budget)
        r2, e2 = _fp._scan_cross_v2_chain(app, ctx, settings, cross_budget)
        return r0 + r1 + r2, e0 + e1 + e2

    # Submit Base first.  Threads are still bounded and all chains retain a budget.
    ctxs.sort(key=lambda ctx: (str(getattr(ctx.config, "slug", "")).lower() != "base", str(getattr(ctx.config, "slug", ""))))
    workers = max(1, min(len(ctxs), worker_cap, _fp._int(settings.get("full_power_parallel_chains", "5"), 5)))
    with _fp.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="power-chain") as ex:
        futures = {ex.submit(scan_one, ctx): ctx for ctx in ctxs}
        for future in _fp.as_completed(futures):
            ctx = futures[future]
            slug = str(getattr(ctx.config, "slug", "")).strip().lower()
            try:
                r, e = future.result()
                rows.extend(r)
                rejected.extend(e)
                if slug == "base":
                    base_rows = sorted(
                        list(r),
                        key=lambda row: _fp._dec(row.get("expected_gross_profit_base"), "0")
                        - _fp._dec(row.get("slippage_reserve_base"), "0"),
                        reverse=True,
                    )[:max_routes]
                    _fp._atomic_write(base_out, base_rows, _fp.LIVE_HEADERS)
            except Exception as exc:
                rejected.append(
                    {
                        "observed_at_epoch": int(time.time()),
                        "chain_id": "",
                        "chain_slug": slug,
                        "route_kind": "FULL_POWER",
                        "route_path": "",
                        "stage": "thread",
                        "reason": f"{type(exc).__name__}:{exc}",
                    }
                )
                if slug == "base":
                    _fp._atomic_write(base_out, [], _fp.LIVE_HEADERS)

    rows.sort(
        key=lambda row: _fp._dec(row.get("expected_gross_profit_base"), "0")
        - _fp._dec(row.get("slippage_reserve_base"), "0"),
        reverse=True,
    )
    rows = rows[:max_routes]
    _fp._atomic_write(out, rows, _fp.LIVE_HEADERS)
    _fp._atomic_rows(rej_path, rejected[-1500:], _fp.POWER_REJECT_HEADERS)
    return out, rows, rejected


def install() -> None:
    if getattr(_fp, "_deep_trading_pipeline_repair_installed", False):
        return

    # EVM history: preserve the final audited _trace.refresh_wallet_history identity;
    # change only the globals it dynamically calls.
    _trace._PREV_REFRESH_WALLET_HISTORY = _nontrace_history
    _trace._refresh_progressive = _progressive_history
    _retry._TRANSIENT_RETRY_COOLDOWN_SECONDS = max(
        int(getattr(_retry, "_TRANSIENT_RETRY_COOLDOWN_SECONDS", 60)),
        300,
    )
    _drainer._DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = max(
        int(getattr(_drainer, "_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS", 60)),
        300,
    )

    # Solana: preserve _leader.historical_ok and refresh_rankings identities used by
    # the final integrity checker; only replace their dynamic evidence predicate.
    _leader._PREV_HISTORICAL_OK = _adaptive_pre_quality_ok
    _leader._quality_failure_reason = _adaptive_quality_failure_reason
    _leader._write_bridge = _write_bridge_with_profile

    # GPT/Base: patch every known binding, including fast_market's by-value import.
    _rotation._scan_full_power_hot_routes = _weighted_scan_full_power_hot_routes
    _fp.scan_full_power_hot_routes = _weighted_scan_full_power_hot_routes
    _fast.scan_full_power_hot_routes = _weighted_scan_full_power_hot_routes

    _fp._deep_trading_pipeline_repair_installed = True
    print(
        "[deep-trading-pipeline-repair] installed=true "
        "evm_history_fallback=etherscan-on-alchemy-pressure "
        "alchemy_circuit=600s base_scan=pressure-aware-weighted "
        "solana_quality=adaptive-two-tier live_median_floors=unchanged "
        "poolcheck_execution_safety=unchanged",
        flush=True,
    )


install()
