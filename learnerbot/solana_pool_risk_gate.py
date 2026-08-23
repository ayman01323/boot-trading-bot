from __future__ import annotations

import threading
import time
from decimal import Decimal
from urllib.parse import quote as urlquote

import requests

from . import solana_live_patch as _live
from . import solana_sibot as _sol

# Canonical LIVE-only pool/mint safety gate learned from the HOOD incident.
# SHADOW research deliberately remains available when external evidence providers
# are unavailable; real capital fails closed.
_sol.DEFAULTS.update({
    "live_pool_reference_probe_sol": ("0.01", "Reference SOL depth used by the pre-entry reverse-sell probe"),
    "live_pool_reference_max_impact_bps": ("200", "Maximum reverse price impact on the reference depth probe"),
    "live_pool_rugcheck_hard_score": ("70", "Maximum RugCheck normalized risk score allowed for LIVE"),
    "live_pool_min_lp_locked_pct": ("50", "Minimum LP locked percentage allowed for unrestricted LIVE"),
    "live_pool_new_pair_cooling_seconds": ("900", "Cooling period for a newly created material pool"),
    "live_pool_soft_risk_cooling_seconds": ("3600", "Cooling period for fresh-pool wash/divergence signals"),
    "live_pool_volume_liquidity_soft_ratio": ("50", "Fresh-pool 24h volume/liquidity ratio soft-risk ceiling"),
    "live_pool_cross_price_soft_ratio": ("5", "Fresh material-pool native-price divergence soft-risk ceiling"),
    "live_pool_material_pair_min_usd": ("100", "Absolute minimum liquidity for a pair to influence pool-quality checks"),
    "live_pool_external_timeout_seconds": ("2.5", "Per-provider timeout for LIVE pool screening"),
    "live_pool_rugcheck_cache_seconds": ("900", "RugCheck per-mint cache TTL"),
    "live_pool_dex_cache_seconds": ("60", "DexScreener per-mint pool-state cache TTL"),
})

_RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
_DEX_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{mint}"

# These constants are hard safety boundaries. Settings may make the gate stricter
# but cannot make it looser.
_MAX_REFERENCE_IMPACT_BPS = Decimal("200")
_MIN_REFERENCE_SOL = Decimal("0.01")
_MAX_REFERENCE_SOL = Decimal("0.02")
_MAX_RUG_SCORE = Decimal("70")
_MIN_LP_LOCKED_PCT = Decimal("50")
_MIN_NEW_POOL_COOLING = 900
_MAX_VOLUME_LIQ_RATIO = Decimal("50")
_MAX_CROSS_PRICE_RATIO = Decimal("5")

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, object]] = {}
_LIQ_HISTORY: dict[str, list[tuple[float, Decimal]]] = {}
_PREV_PROCESS = None


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _decision(kind: str, code: str, reason: str, evidence: dict | None = None) -> dict:
    return {"decision": kind, "reason_code": code, "reason": reason, "evidence": dict(evidence or {})}


def _severity(result: dict) -> int:
    return {"PASS": 0, "COOLING": 1, "SHADOW_ONLY": 2, "HARD_BLOCK": 3}.get(
        str((result or {}).get("decision") or "HARD_BLOCK").upper(), 3
    )


def _merge(*results: dict) -> dict:
    values = [dict(v) for v in results if v]
    if not values:
        return _decision("PASS", "POOL_RISK_PASS", "pool risk checks passed")
    worst = max(values, key=_severity)
    evidence = {}
    for value in values:
        evidence.update(value.get("evidence") or {})
    worst["evidence"] = evidence
    return worst


def _cfg_d(cfg: dict, key: str, default: str) -> Decimal:
    return _sol._dec(cfg.get(key), default)


def _timeout(cfg: dict) -> float:
    return float(max(Decimal("0.5"), min(Decimal("3"), _cfg_d(cfg, "live_pool_external_timeout_seconds", "2.5"))))


def _cache_get(provider: str, mint: str, ttl: float):
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get((provider, mint))
        if item and now - item[0] <= ttl:
            return item[1]
    return None


def _cache_put(provider: str, mint: str, value) -> None:
    with _LOCK:
        _CACHE[(provider, mint)] = (time.monotonic(), value)
        if len(_CACHE) > 512:
            cutoff = time.monotonic() - 3600
            for key, item in list(_CACHE.items()):
                if item[0] < cutoff:
                    _CACHE.pop(key, None)


def _fetch_json(provider: str, url: str, mint: str, ttl: float, timeout: float):
    cached = _cache_get(provider, mint, ttl)
    if cached is not None:
        return cached, True
    response = requests.get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "boot-trading-bot/pool-risk-gate"},
    )
    response.raise_for_status()
    value = response.json()
    _cache_put(provider, mint, value)
    return value, False


def _risk_text(risk: dict) -> str:
    return " ".join(str(risk.get(k) or "") for k in ("name", "description", "value")).lower()


def evaluate_rugcheck(summary: dict, cfg: dict) -> dict:
    if not isinstance(summary, dict):
        return _decision("HARD_BLOCK", "RUGCHECK_INVALID", "RugCheck returned an invalid report")

    risks = [r for r in (summary.get("risks") or []) if isinstance(r, dict)]
    score = _d(summary.get("score_normalised"), 0)
    lp_locked = _d(summary.get("lpLockedPct"), 0)
    evidence = {
        "rugcheck_score_normalised": score,
        "rugcheck_lp_locked_pct": lp_locked,
        "rugcheck_token_program": str(summary.get("tokenProgram") or ""),
        "rugcheck_risks": [str(r.get("name") or "")[:100] for r in risks[:12]],
    }

    score_limit = min(_MAX_RUG_SCORE, max(Decimal(1), _cfg_d(cfg, "live_pool_rugcheck_hard_score", "70")))
    if score >= score_limit:
        return _decision(
            "HARD_BLOCK", "TOKEN_SECURITY_SEVERE",
            f"RugCheck normalized risk score {score} >= hard ceiling {score_limit}", evidence,
        )

    dangerous = (
        "freeze authority", "mint authority", "permanent delegate", "honeypot",
        "rugged", "blacklist", "non-transferable", "default account state",
    )
    for risk in risks:
        level = str(risk.get("level") or "").lower()
        text = _risk_text(risk)
        if level in {"danger", "critical", "severe"} or any(term in text for term in dangerous):
            name = str(risk.get("name") or level)[:120]
            evidence["rugcheck_blocking_risk"] = name
            return _decision("HARD_BLOCK", "TOKEN_SECURITY_SEVERE", f"RugCheck severe token/pool risk: {name}", evidence)

    # Concentrated/unlocked LP is a medium-confidence source signal. Do not call
    # it fraud; keep the candidate in SHADOW until the pool becomes safer.
    lp_floor = max(_MIN_LP_LOCKED_PCT, _cfg_d(cfg, "live_pool_min_lp_locked_pct", "50"))
    concentrated = any("lp provider" in _risk_text(r) or "liquidity provider" in _risk_text(r) for r in risks)
    if lp_locked < lp_floor or concentrated:
        return _decision(
            "SHADOW_ONLY", "LP_CONCENTRATION_RISK",
            f"LP safety is not strong enough for LIVE: locked={lp_locked}% required>={lp_floor}%", evidence,
        )
    return _decision("PASS", "RUGCHECK_PASS", "RugCheck safety screen passed", evidence)


def _liq_usd(pair: dict) -> Decimal:
    return max(Decimal(0), _d((pair.get("liquidity") or {}).get("usd"), 0))


def _native_price(pair: dict) -> Decimal:
    return max(Decimal(0), _d(pair.get("priceNative"), 0))


def _age_seconds(pair: dict, now_epoch: float) -> float | None:
    try:
        raw = float(pair.get("pairCreatedAt") or 0)
    except Exception:
        return None
    if raw <= 0:
        return None
    created = raw / 1000 if raw > 10_000_000_000 else raw
    return max(0.0, now_epoch - created)


def _prior_liquidity(mint: str, now_epoch: float, current: Decimal) -> Decimal | None:
    with _LOCK:
        history = _LIQ_HISTORY.setdefault(mint, [])
        prior = next((v for ts, v in reversed(history) if now_epoch - ts <= 3600 and v > 0), None)
        history.append((now_epoch, current))
        _LIQ_HISTORY[mint] = [(ts, v) for ts, v in history if now_epoch - ts <= 3600][-60:]
        return prior


def evaluate_dexscreener(pairs, cfg: dict, *, mint: str, now_epoch: float | None = None) -> dict:
    now_epoch = float(time.time() if now_epoch is None else now_epoch)
    if not isinstance(pairs, list):
        return _decision("HARD_BLOCK", "DEX_DATA_INVALID", "DexScreener returned invalid pool data")
    pairs = [p for p in pairs if isinstance(p, dict) and str(p.get("chainId") or "solana").lower() == "solana"]
    if not pairs:
        return _decision(
            "COOLING", "DEX_INDEX_PENDING",
            "no indexed Solana pool yet; keep the mint in SHADOW while indexing/liquidity stabilises",
            {"dex_pair_count": 0},
        )

    total_liq = sum((_liq_usd(p) for p in pairs), Decimal(0))
    max_liq = max((_liq_usd(p) for p in pairs), default=Decimal(0))
    volume = sum((max(Decimal(0), _d((p.get("volume") or {}).get("h24"), 0)) for p in pairs), Decimal(0))
    volume_ratio = volume / max(Decimal(1), total_liq)

    # Ignore tiny spam pools: a pair must satisfy BOTH an absolute floor and 10%
    # of the dominant pair's liquidity before its age/price can affect a decision.
    absolute_floor = max(Decimal(1), _cfg_d(cfg, "live_pool_material_pair_min_usd", "100"))
    material_floor = max(absolute_floor, max_liq * Decimal("0.10")) if max_liq > 0 else absolute_floor
    material = [p for p in pairs if _liq_usd(p) >= material_floor]
    if not material:
        material = [max(pairs, key=_liq_usd)]

    ages = [v for v in (_age_seconds(p, now_epoch) for p in material) if v is not None]
    youngest = min(ages) if ages else None
    prices = [_native_price(p) for p in material if _native_price(p) > 0]
    cross_ratio = max(prices) / min(prices) if len(prices) >= 2 and min(prices) > 0 else Decimal(1)
    sol_quote_liq = sum(
        (max(Decimal(0), _d((p.get("liquidity") or {}).get("quote"), 0)) for p in pairs
         if str((p.get("quoteToken") or {}).get("address") or "") == _sol.WSOL_MINT),
        Decimal(0),
    )

    evidence = {
        "dex_pair_count": len(pairs),
        "dex_material_pair_count": len(material),
        "dex_ids": sorted({str(p.get("dexId") or "unknown") for p in material}),
        "dex_liquidity_usd_total": total_liq,
        "dex_liquidity_usd_max_pair": max_liq,
        "dex_sol_quote_liquidity": sol_quote_liq,
        "dex_volume_h24_usd": volume,
        "dex_volume_liquidity_ratio": volume_ratio,
        "dex_youngest_material_pair_age_seconds": youngest,
        "dex_cross_pool_price_ratio": cross_ratio,
    }

    prior = _prior_liquidity(mint, now_epoch, total_liq)
    if prior and prior > 0:
        retained = total_liq * Decimal(100) / prior
        evidence.update({"dex_prior_liquidity_usd_within_1h": prior, "dex_liquidity_retained_pct": retained})
        if retained < Decimal("30"):
            return _decision(
                "HARD_BLOCK", "POOL_LIQUIDITY_COLLAPSE",
                f"observed pool liquidity retained only {retained:.2f}% of a prior <=1h snapshot", evidence,
            )

    cooling = max(_MIN_NEW_POOL_COOLING, _sol._int(cfg.get("live_pool_new_pair_cooling_seconds"), 900))
    soft_window = max(cooling, _sol._int(cfg.get("live_pool_soft_risk_cooling_seconds"), 3600))
    volume_limit = min(_MAX_VOLUME_LIQ_RATIO, max(Decimal(1), _cfg_d(cfg, "live_pool_volume_liquidity_soft_ratio", "50")))
    cross_limit = min(_MAX_CROSS_PRICE_RATIO, max(Decimal("1.1"), _cfg_d(cfg, "live_pool_cross_price_soft_ratio", "5")))

    if youngest is not None and youngest < cooling:
        evidence["cooling_remaining_seconds"] = int(cooling - youngest)
        return _decision("COOLING", "POOL_NEW_COOLING", f"material pool age {int(youngest)}s is inside {cooling}s LIVE cooling period", evidence)
    if youngest is not None and youngest < soft_window and volume_ratio > volume_limit:
        evidence["cooling_remaining_seconds"] = int(soft_window - youngest)
        return _decision("COOLING", "WASH_VOLUME_SOFT_RISK", f"fresh-pool 24h volume/liquidity ratio {volume_ratio:.2f} exceeds {volume_limit}", evidence)
    if youngest is not None and youngest < soft_window and cross_ratio > cross_limit:
        evidence["cooling_remaining_seconds"] = int(soft_window - youngest)
        return _decision("COOLING", "CROSS_POOL_PRICE_DISCONTINUITY", f"fresh material pools disagree by {cross_ratio:.2f}x on native price (limit {cross_limit}x)", evidence)
    return _decision("PASS", "DEX_POOL_PASS", "DexScreener pool-state screen passed", evidence)


def external_pool_check(mint: str, cfg: dict) -> dict:
    encoded = urlquote(str(mint), safe="")
    timeout = _timeout(cfg)
    try:
        rug, cached = _fetch_json(
            "rugcheck", _RUGCHECK_URL.format(mint=encoded), str(mint),
            float(max(60, _sol._int(cfg.get("live_pool_rugcheck_cache_seconds"), 900))), timeout,
        )
    except Exception as exc:
        return _decision(
            "HARD_BLOCK", "RUGCHECK_UNAVAILABLE",
            f"RugCheck unavailable ({type(exc).__name__}); LIVE fails closed while SHADOW remains available",
            {"rugcheck_available": False},
        )
    rug_result = evaluate_rugcheck(rug, cfg)
    rug_result["evidence"]["rugcheck_cached"] = bool(cached)
    if _severity(rug_result) >= 2:
        return rug_result

    try:
        dex, cached = _fetch_json(
            "dexscreener", _DEX_URL.format(mint=encoded), str(mint),
            float(max(15, _sol._int(cfg.get("live_pool_dex_cache_seconds"), 60))), timeout,
        )
    except Exception as exc:
        return _decision(
            "HARD_BLOCK", "DEXSCREENER_UNAVAILABLE",
            f"DexScreener unavailable ({type(exc).__name__}); LIVE fails closed while SHADOW remains available",
            {**rug_result["evidence"], "dexscreener_available": False},
        )
    dex_result = evaluate_dexscreener(dex, cfg, mint=str(mint))
    dex_result["evidence"]["dexscreener_cached"] = bool(cached)
    return _merge(rug_result, dex_result)


def reference_reverse_depth_check(app, event: dict, cfg: dict, *, probe_sol: Decimal | None = None) -> dict:
    leader_sol = max(Decimal(0), _d(event.get("sol_amount"), 0))
    leader_raw = max(Decimal(0), _d(event.get("token_amount_raw"), 0))
    evidence = {"reference_leader_sol": leader_sol, "reference_leader_token_raw": leader_raw}
    if leader_sol <= 0 or leader_raw <= 0:
        return _decision("HARD_BLOCK", "REFERENCE_DEPTH_UNAVAILABLE", "leader execution lacks price evidence for reverse-depth probing", evidence)

    configured = max(_MIN_REFERENCE_SOL, _cfg_d(cfg, "live_pool_reference_probe_sol", "0.01"))
    reference_sol = min(_MAX_REFERENCE_SOL, max(configured, Decimal(str(probe_sol or 0))))
    probe_raw = max(1, int(leader_raw * reference_sol / leader_sol))
    evidence.update({"reference_probe_sol": reference_sol, "reference_probe_token_raw": probe_raw})

    try:
        reverse = _sol.jupiter_quote(app, str(event.get("mint") or ""), _sol.WSOL_MINT, probe_raw)
    except Exception as exc:
        return _decision("HARD_BLOCK", "REFERENCE_DEPTH_QUOTE_FAILED", f"reference reverse-depth quote failed ({type(exc).__name__})", evidence)

    from .solana_entry_exit_liquidity_preflight_patch import _quote_price_impact_bps
    impact = _quote_price_impact_bps(reverse)
    out_raw = _sol._int(reverse.get("outAmount") or reverse.get("outputAmount"), 0)
    evidence.update({"reference_reverse_out_lamports": out_raw, "reference_reverse_price_impact_bps": impact})
    if impact is None:
        return _decision("HARD_BLOCK", "REFERENCE_DEPTH_UNAVAILABLE", "reference reverse quote did not report price impact", evidence)
    if out_raw <= 0:
        return _decision("HARD_BLOCK", "THIN_REFERENCE_DEPTH", "reference reverse quote returned no SOL output", evidence)

    impact_limit = min(_MAX_REFERENCE_IMPACT_BPS, max(Decimal(1), _cfg_d(cfg, "live_pool_reference_max_impact_bps", "200")))
    evidence["reference_reverse_impact_limit_bps"] = impact_limit
    if impact > impact_limit:
        return _decision("HARD_BLOCK", "THIN_REFERENCE_DEPTH", f"reference reverse price impact {impact:.2f} bps exceeds {impact_limit:.0f} bps", evidence)

    recovered = Decimal(out_raw) / Decimal(1_000_000_000)
    loss_pct = max(Decimal(0), (Decimal(1) - recovered / reference_sol) * Decimal(100))
    evidence.update({"reference_reverse_recovered_sol": recovered, "reference_reverse_loss_pct": loss_pct})
    if loss_pct > max(Decimal(0), _cfg_d(cfg, "max_roundtrip_loss_pct", "3")):
        return _decision("HARD_BLOCK", "THIN_REFERENCE_DEPTH", f"reference reverse value loss {loss_pct:.3f}% exceeds round-trip limit", evidence)
    return _decision("PASS", "REFERENCE_DEPTH_PASS", "reference reverse-depth probe passed", evidence)


def evaluate_live_pool_risk(app, event: dict, cfg: dict, *, probe_sol: Decimal | None = None) -> dict:
    external = external_pool_check(str(event.get("mint") or ""), cfg)
    if _severity(external) > 0:
        return external
    return _merge(external, reference_reverse_depth_check(app, event, cfg, probe_sol=probe_sol))


def _eligible_live_users(app, event: dict, cfg: dict) -> list[tuple[str, Decimal]]:
    out = []
    for user in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        if _sol._leader_rank(app, tid, event.get("leader_wallet")) is None:
            continue
        limit = max(1, min(5, _sol._int(cfg.get("live_max_positions"), 1)))
        if _live._open_live_count(app, tid) >= limit or _sol._open_position(app, tid, event.get("mint")):
            continue
        allocation, _ = _live.live_limits(app, tid, cfg)
        out.append((tid, Decimal(str(allocation))))
    return out


def process_leader_event_with_pool_risk(app, event: dict):
    if _PREV_PROCESS is None:
        return []
    if str(event.get("action") or "").upper() != "BUY":
        return _PREV_PROCESS(app, event)

    cfg = _sol.settings(app)
    eligible = _eligible_live_users(app, event, cfg)
    if not eligible:
        return _PREV_PROCESS(app, event)

    probe_sol = max((allocation for _, allocation in eligible), default=Decimal(0))
    result = evaluate_live_pool_risk(app, event, cfg, probe_sol=probe_sol)
    if _severity(result) > 0:
        reason = f"{result['reason_code']}: {result['reason']}"
        print("[solana-pool-risk] decision=%s code=%s mint=%s reason=%s" % (
            result["decision"], result["reason_code"], str(event.get("mint") or ""), result["reason"][:240]
        ))
        return [
            {"telegram_id": tid, "action": "REJECT", "reason": reason, "pool_risk_code": result["reason_code"]}
            for tid, _ in eligible
        ]

    print("[solana-pool-risk] decision=PASS code=POOL_RISK_PASS mint=%s probe_sol=%s" % (
        str(event.get("mint") or ""), result.get("evidence", {}).get("reference_probe_sol", "")
    ))
    return _PREV_PROCESS(app, event)


def install() -> None:
    global _PREV_PROCESS
    if getattr(_live, "_pool_risk_gate_installed", False):
        return
    _PREV_PROCESS = _live.process_leader_event
    _live.process_leader_event = process_leader_event_with_pool_risk
    _live._pool_risk_gate_installed = True
    print(
        "[solana-pool-risk] installed=true live_only=true shadow_external_outage_unaffected=true "
        "reference_probe_sol=0.01 reference_impact_hard_cap_bps=200 rugcheck=true dexscreener=true"
    )
