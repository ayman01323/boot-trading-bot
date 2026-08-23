from __future__ import annotations

import threading
import time
from decimal import Decimal
from urllib.parse import quote as urlquote

import requests

from . import solana_live_patch as _live
from . import solana_sibot as _sol

# HOOD incident learning gate. This module is deliberately LIVE-only: SHADOW
# research must continue when an external screening service is unavailable, but
# real capital fails closed. The gate runs after DB-only leader quality filters
# and before the existing Jupiter entry preflight / signing path.
_sol.DEFAULTS.update({
    "live_pool_reference_probe_sol": (
        "0.01",
        "Reference SOL depth used to prove a copied token remains sellable before LIVE entry",
    ),
    "live_pool_reference_max_impact_bps": (
        "200",
        "Maximum price impact on the small reference reverse-sell depth probe",
    ),
    "live_pool_rugcheck_hard_score": (
        "70",
        "Maximum RugCheck normalized risk score before LIVE hard block",
    ),
    "live_pool_min_lp_locked_pct": (
        "50",
        "Minimum RugCheck LP locked percentage for unrestricted LIVE entry",
    ),
    "live_pool_new_pair_cooling_seconds": (
        "900",
        "Minimum cooling period for a newly created material Solana pool",
    ),
    "live_pool_soft_risk_cooling_seconds": (
        "3600",
        "Cooling window for extreme fresh-pool volume/depth or price-discontinuity signals",
    ),
    "live_pool_volume_liquidity_soft_ratio": (
        "50",
        "Fresh-pool 24h volume/liquidity ratio that requires cooling before LIVE entry",
    ),
    "live_pool_cross_price_soft_ratio": (
        "5",
        "Material cross-pool native-price divergence ratio that requires cooling on a fresh pool",
    ),
    "live_pool_material_pair_min_usd": (
        "100",
        "Minimum pair liquidity considered material for pool-age and cross-price checks",
    ),
    "live_pool_external_timeout_seconds": (
        "2.5",
        "Per-provider timeout for cached RugCheck and DexScreener LIVE pool screening",
    ),
    "live_pool_rugcheck_cache_seconds": (
        "900",
        "RugCheck per-mint cache TTL",
    ),
    "live_pool_dex_cache_seconds": (
        "60",
        "DexScreener per-mint pool-state cache TTL",
    ),
})

_RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
_DEX = "https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
_HARD_REFERENCE_MAX_BPS = Decimal("200")
_HARD_RUG_SCORE_MAX = Decimal("70")
_HARD_REFERENCE_MIN_SOL = Decimal("0.01")
_HARD_REFERENCE_MAX_SOL = Decimal("0.02")
_MIN_LP_LOCKED_FLOOR = Decimal("50")
_MIN_NEW_POOL_COOLING_SECONDS = 15 * 60
_MAX_VOLUME_LIQ_RATIO = Decimal("50")
_MAX_CROSS_PRICE_RATIO = Decimal("5")

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, object]] = {}
_LIQUIDITY_HISTORY: dict[str, list[tuple[float, Decimal]]] = {}
_PREV_PROCESS = None


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _decision(decision: str, code: str, reason: str, evidence: dict | None = None) -> dict:
    return {
        "decision": str(decision),
        "reason_code": str(code),
        "reason": str(reason),
        "evidence": dict(evidence or {}),
    }


def _severity(value: dict) -> int:
    return {
        "PASS": 0,
        "COOLING": 1,
        "SHADOW_ONLY": 2,
        "HARD_BLOCK": 3,
    }.get(str((value or {}).get("decision") or "HARD_BLOCK").upper(), 3)


def _merge(*values: dict) -> dict:
    vals = [dict(v or {}) for v in values if v]
    if not vals:
        return _decision("PASS", "POOL_RISK_PASS", "pool risk checks passed", {})
    worst = max(vals, key=_severity)
    evidence = {}
    for value in vals:
        evidence.update(dict(value.get("evidence") or {}))
    out = dict(worst)
    out["evidence"] = evidence
    return out


def _cfg_decimal(cfg: dict, key: str, default: str) -> Decimal:
    return _sol._dec(cfg.get(key), default)


def _timeout(cfg: dict) -> float:
    # Keep the external dependency bounded even if a setting is malformed.
    return float(max(Decimal("0.5"), min(Decimal("3.0"), _cfg_decimal(cfg, "live_pool_external_timeout_seconds", "2.5"))))


def _cache_get(provider: str, mint: str, ttl: float):
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get((provider, mint))
        if item and now - item[0] <= ttl:
            return item[1]
    return None


def _cache_put(provider: str, mint: str, value):
    with _CACHE_LOCK:
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
        headers={"Accept": "application/json", "User-Agent": "boot-trading-bot/pool-risk-check"},
    )
    response.raise_for_status()
    value = response.json()
    _cache_put(provider, mint, value)
    return value, False


def _risk_text(risk: dict) -> str:
    return " ".join(
        str(risk.get(key) or "")
        for key in ("name", "description", "value")
    ).strip().lower()


def evaluate_rugcheck(summary: dict, cfg: dict) -> dict:
    if not isinstance(summary, dict):
        return _decision("HARD_BLOCK", "RUGCHECK_INVALID", "RugCheck returned an invalid report", {})

    risks = [r for r in (summary.get("risks") or []) if isinstance(r, dict)]
    score = _d(summary.get("score_normalised"), 0)
    lp_locked = _d(summary.get("lpLockedPct"), 0)
    token_program = str(summary.get("tokenProgram") or "")
    labels = [str(r.get("name") or "")[:100] for r in risks[:12]]
    evidence = {
        "rugcheck_score_normalised": score,
        "rugcheck_lp_locked_pct": lp_locked,
        "rugcheck_token_program": token_program,
        "rugcheck_risks": labels,
    }

    hard_score = min(
        _HARD_RUG_SCORE_MAX,
        max(Decimal(1), _cfg_decimal(cfg, "live_pool_rugcheck_hard_score", "70")),
    )
    if score >= hard_score:
        return _decision(
            "HARD_BLOCK",
            "TOKEN_SECURITY_SEVERE",
            f"RugCheck normalized risk score {score} >= hard ceiling {hard_score}",
            evidence,
        )

    dangerous_terms = (
        "freeze authority",
        "mint authority",
        "permanent delegate",
        "honeypot",
        "rugged",
        "blacklist",
        "non-transferable",
        "default account state",
    )
    for risk in risks:
        level = str(risk.get("level") or "").strip().lower()
        text = _risk_text(risk)
        if level in {"danger", "critical", "severe"} or any(term in text for term in dangerous_terms):
            evidence["rugcheck_blocking_risk"] = str(risk.get("name") or level)[:120]
            return _decision(
                "HARD_BLOCK",
                "TOKEN_SECURITY_SEVERE",
                f"RugCheck severe token/pool risk: {evidence['rugcheck_blocking_risk']}",
                evidence,
            )

    # LP/provider concentration is a medium-confidence single-source signal.
    # It does not allege fraud, but LIVE capital stays SHADOW-only until the
    # concentration/lock state improves. Low-liquidity labels alone are not used
    # here because executable Jupiter depth is tested independently below.
    lp_floor = max(
        _MIN_LP_LOCKED_FLOOR,
        _cfg_decimal(cfg, "live_pool_min_lp_locked_pct", "50"),
    )
    concentration = any(
        ("lp provider" in _risk_text(r) or "liquidity provider" in _risk_text(r))
        for r in risks
    )
    if lp_locked < lp_floor or concentration:
        return _decision(
            "SHADOW_ONLY",
            "LP_CONCENTRATION_RISK",
            f"LP safety is not strong enough for LIVE: locked={lp_locked}% required>={lp_floor}%",
            evidence,
        )

    return _decision("PASS", "RUGCHECK_PASS", "RugCheck safety screen passed", evidence)


def _pair_created_seconds(pair: dict, now_epoch: float) -> float | None:
    try:
        raw = float(pair.get("pairCreatedAt") or 0)
    except Exception:
        return None
    if raw <= 0:
        return None
    # DexScreener reports epoch milliseconds today; tolerate seconds defensively.
    created = raw / 1000.0 if raw > 10_000_000_000 else raw
    return max(0.0, now_epoch - created)


def _pair_liquidity_usd(pair: dict) -> Decimal:
    return max(Decimal(0), _d((pair.get("liquidity") or {}).get("usd"), 0))


def _pair_price_native(pair: dict) -> Decimal:
    return max(Decimal(0), _d(pair.get("priceNative"), 0))


def _record_liquidity_snapshot(mint: str, now_epoch: float, total_usd: Decimal) -> Decimal | None:
    with _CACHE_LOCK:
        history = _LIQUIDITY_HISTORY.setdefault(mint, [])
        prior = None
        for ts, value in reversed(history):
            if now_epoch - ts <= 3600 and value > 0:
                prior = value
                break
        history.append((now_epoch, total_usd))
        _LIQUIDITY_HISTORY[mint] = [(ts, value) for ts, value in history if now_epoch - ts <= 3600][-60:]
        return prior


def evaluate_dexscreener(pairs, cfg: dict, *, mint: str, now_epoch: float | None = None) -> dict:
    now_epoch = float(time.time() if now_epoch is None else now_epoch)
    if not isinstance(pairs, list):
        return _decision("HARD_BLOCK", "DEX_DATA_INVALID", "DexScreener returned invalid pool data", {})
    pairs = [p for p in pairs if isinstance(p, dict) and str(p.get("chainId") or "solana").lower() == "solana"]
    if not pairs:
        return _decision(
            "COOLING",
            "DEX_INDEX_PENDING",
            "No indexed Solana pool is available yet; keep the mint in SHADOW while indexing/liquidity stabilises",
            {"dex_pair_count": 0},
        )

    liquidity_total = sum((_pair_liquidity_usd(p) for p in pairs), Decimal(0))
    liquidity_max = max((_pair_liquidity_usd(p) for p in pairs), default=Decimal(0))
    volume_h24 = sum((max(Decimal(0), _d((p.get("volume") or {}).get("h24"), 0)) for p in pairs), Decimal(0))
    volume_liq_ratio = volume_h24 / max(Decimal(1), liquidity_total)

    material_min = max(Decimal(1), _cfg_decimal(cfg, "live_pool_material_pair_min_usd", "100"))
    relative_floor = liquidity_max * Decimal("0.10")
    material_floor = min(material_min, relative_floor) if liquidity_max > 0 else material_min
    material = [p for p in pairs if _pair_liquidity_usd(p) >= material_floor]
    if not material:
        material = list(pairs)

    ages = [age for age in (_pair_created_seconds(p, now_epoch) for p in material) if age is not None]
    youngest_age = min(ages) if ages else None
    prices = [_pair_price_native(p) for p in material if _pair_price_native(p) > 0]
    cross_ratio = (max(prices) / min(prices)) if len(prices) >= 2 and min(prices) > 0 else Decimal(1)
    dex_ids = sorted({str(p.get("dexId") or "unknown") for p in material})

    sol_quote_liquidity = Decimal(0)
    for p in pairs:
        quote_token = str((p.get("quoteToken") or {}).get("address") or "")
        if quote_token == _sol.WSOL_MINT:
            sol_quote_liquidity += max(Decimal(0), _d((p.get("liquidity") or {}).get("quote"), 0))

    evidence = {
        "dex_pair_count": len(pairs),
        "dex_material_pair_count": len(material),
        "dex_ids": dex_ids,
        "dex_liquidity_usd_total": liquidity_total,
        "dex_liquidity_usd_max_pair": liquidity_max,
        "dex_sol_quote_liquidity": sol_quote_liquidity,
        "dex_volume_h24_usd": volume_h24,
        "dex_volume_liquidity_ratio": volume_liq_ratio,
        "dex_youngest_material_pair_age_seconds": youngest_age,
        "dex_cross_pool_price_ratio": cross_ratio,
    }

    prior = _record_liquidity_snapshot(mint, now_epoch, liquidity_total)
    if prior and prior > 0:
        retained = liquidity_total * Decimal(100) / prior
        evidence["dex_prior_liquidity_usd_within_1h"] = prior
        evidence["dex_liquidity_retained_pct"] = retained
        if retained < Decimal("30"):
            return _decision(
                "HARD_BLOCK",
                "POOL_LIQUIDITY_COLLAPSE",
                f"observed pool liquidity retained only {retained:.2f}% of a prior <=1h snapshot",
                evidence,
            )

    new_cooling = max(
        _MIN_NEW_POOL_COOLING_SECONDS,
        _sol._int(cfg.get("live_pool_new_pair_cooling_seconds"), 900),
    )
    soft_window = max(new_cooling, _sol._int(cfg.get("live_pool_soft_risk_cooling_seconds"), 3600))
    volume_limit = min(
        _MAX_VOLUME_LIQ_RATIO,
        max(Decimal(1), _cfg_decimal(cfg, "live_pool_volume_liquidity_soft_ratio", "50")),
    )
    cross_limit = min(
        _MAX_CROSS_PRICE_RATIO,
        max(Decimal("1.1"), _cfg_decimal(cfg, "live_pool_cross_price_soft_ratio", "5")),
    )

    if youngest_age is not None and youngest_age < new_cooling:
        evidence["cooling_remaining_seconds"] = int(new_cooling - youngest_age)
        return _decision(
            "COOLING",
            "POOL_NEW_COOLING",
            f"material pool age {int(youngest_age)}s is inside {new_cooling}s LIVE cooling period",
            evidence,
        )
    if youngest_age is not None and youngest_age < soft_window and volume_liq_ratio > volume_limit:
        evidence["cooling_remaining_seconds"] = int(soft_window - youngest_age)
        return _decision(
            "COOLING",
            "WASH_VOLUME_SOFT_RISK",
            f"fresh-pool 24h volume/liquidity ratio {volume_liq_ratio:.2f} exceeds {volume_limit}",
            evidence,
        )
    if youngest_age is not None and youngest_age < soft_window and cross_ratio > cross_limit:
        evidence["cooling_remaining_seconds"] = int(soft_window - youngest_age)
        return _decision(
            "COOLING",
            "CROSS_POOL_PRICE_DISCONTINUITY",
            f"fresh material pools disagree by {cross_ratio:.2f}x on native price (limit {cross_limit}x)",
            evidence,
        )

    return _decision("PASS", "DEX_POOL_PASS", "DexScreener pool-state screen passed", evidence)


def external_pool_check(mint: str, cfg: dict) -> dict:
    safe_mint = urlquote(str(mint), safe="")
    timeout = _timeout(cfg)
    try:
        rug_ttl = float(max(60, _sol._int(cfg.get("live_pool_rugcheck_cache_seconds"), 900)))
        rug, rug_cached = _fetch_json("rugcheck", _RUGCHECK.format(mint=safe_mint), str(mint), rug_ttl, timeout)
    except Exception as exc:
        return _decision(
            "HARD_BLOCK",
            "RUGCHECK_UNAVAILABLE",
            f"RugCheck unavailable ({type(exc).__name__}); LIVE fails closed while SHADOW remains available",
            {"rugcheck_available": False},
        )
    rug_decision = evaluate_rugcheck(rug, cfg)
    rug_decision["evidence"]["rugcheck_cached"] = bool(rug_cached)
    if _severity(rug_decision) >= _severity({"decision": "SHADOW_ONLY"}):
        return rug_decision

    try:
        dex_ttl = float(max(15, _sol._int(cfg.get("live_pool_dex_cache_seconds"), 60)))
        dex, dex_cached = _fetch_json("dexscreener", _DEX.format(mint=safe_mint), str(mint), dex_ttl, timeout)
    except Exception as exc:
        return _decision(
            "HARD_BLOCK",
            "DEXSCREENER_UNAVAILABLE",
            f"DexScreener unavailable ({type(exc).__name__}); LIVE fails closed while SHADOW remains available",
            {**rug_decision["evidence"], "dexscreener_available": False},
        )
    dex_decision = evaluate_dexscreener(dex, cfg, mint=str(mint))
    dex_decision["evidence"]["dexscreener_cached"] = bool(dex_cached)
    return _merge(rug_decision, dex_decision)


def reference_reverse_depth_check(app, event: dict, cfg: dict, *, probe_sol: Decimal | None = None) -> dict:
    leader_sol = max(Decimal(0), _d(event.get("sol_amount"), 0))
    leader_raw = max(Decimal(0), _d(event.get("token_amount_raw"), 0))
    evidence = {
        "reference_leader_sol": leader_sol,
        "reference_leader_token_raw": leader_raw,
    }
    if leader_sol <= 0 or leader_raw <= 0:
        return _decision(
            "HARD_BLOCK",
            "REFERENCE_DEPTH_UNAVAILABLE",
            "leader execution does not contain enough price evidence for a reference reverse-depth probe",
            evidence,
        )

    configured_probe = max(
        _HARD_REFERENCE_MIN_SOL,
        _cfg_decimal(cfg, "live_pool_reference_probe_sol", "0.01"),
    )
    reference_sol = min(_HARD_REFERENCE_MAX_SOL, max(configured_probe, Decimal(str(probe_sol or 0))))
    probe_raw = max(1, int(leader_raw * reference_sol / leader_sol))
    evidence.update({
        "reference_probe_sol": reference_sol,
        "reference_probe_token_raw": probe_raw,
    })

    try:
        reverse = _sol.jupiter_quote(app, str(event.get("mint") or ""), _sol.WSOL_MINT, probe_raw)
    except Exception as exc:
        return _decision(
            "HARD_BLOCK",
            "REFERENCE_DEPTH_QUOTE_FAILED",
            f"reference reverse-depth quote failed ({type(exc).__name__})",
            evidence,
        )

    from .solana_entry_exit_liquidity_preflight_patch import _quote_price_impact_bps

    impact = _quote_price_impact_bps(reverse)
    out_raw = _sol._int(reverse.get("outAmount") or reverse.get("outputAmount"), 0)
    evidence["reference_reverse_out_lamports"] = out_raw
    evidence["reference_reverse_price_impact_bps"] = impact
    if impact is None:
        return _decision(
            "HARD_BLOCK",
            "REFERENCE_DEPTH_UNAVAILABLE",
            "reference reverse quote did not report price impact",
            evidence,
        )
    if out_raw <= 0:
        return _decision("HARD_BLOCK", "THIN_REFERENCE_DEPTH", "reference reverse quote returned no SOL output", evidence)

    impact_limit = min(
        _HARD_REFERENCE_MAX_BPS,
        max(Decimal(1), _cfg_decimal(cfg, "live_pool_reference_max_impact_bps", "200")),
    )
    evidence["reference_reverse_impact_limit_bps"] = impact_limit
    if impact > impact_limit:
        return _decision(
            "HARD_BLOCK",
            "THIN_REFERENCE_DEPTH",
            f"reference reverse price impact {impact:.2f} bps exceeds {impact_limit:.0f} bps",
            evidence,
        )

    recovered_sol = Decimal(out_raw) / Decimal(1_000_000_000)
    loss_pct = max(Decimal(0), (Decimal(1) - recovered_sol / reference_sol) * Decimal(100))
    evidence["reference_reverse_recovered_sol"] = recovered_sol
    evidence["reference_reverse_loss_pct"] = loss_pct
    # This is deliberately aligned to the existing 3% round-trip quality bar.
    if loss_pct > max(Decimal(0), _cfg_decimal(cfg, "max_roundtrip_loss_pct", "3")):
        return _decision(
            "HARD_BLOCK",
            "THIN_REFERENCE_DEPTH",
            f"reference reverse value loss {loss_pct:.3f}% exceeds configured round-trip limit",
            evidence,
        )

    return _decision("PASS", "REFERENCE_DEPTH_PASS", "reference reverse-depth probe passed", evidence)


def evaluate_live_pool_risk(app, event: dict, cfg: dict, *, probe_sol: Decimal | None = None) -> dict:
    external = external_pool_check(str(event.get("mint") or ""), cfg)
    if _severity(external) > 0:
        return external
    depth = reference_reverse_depth_check(app, event, cfg, probe_sol=probe_sol)
    return _merge(external, depth)


def _eligible_live_buy_users(app, event: dict, cfg: dict) -> list[tuple[str, Decimal]]:
    eligible: list[tuple[str, Decimal]] = []
    for user in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        if _sol._leader_rank(app, tid, event.get("leader_wallet")) is None:
            continue
        max_positions = max(1, min(5, _sol._int(cfg.get("live_max_positions"), 1)))
        if _live._open_live_count(app, tid) >= max_positions or _sol._open_position(app, tid, event.get("mint")):
            continue
        allocation, _ = _live.live_limits(app, tid, cfg)
        eligible.append((tid, Decimal(str(allocation))))
    return eligible


def process_leader_event_with_pool_risk(app, event: dict):
    global _PREV_PROCESS
    if str(event.get("action") or "").upper() != "BUY" or _PREV_PROCESS is None:
        return _PREV_PROCESS(app, event) if _PREV_PROCESS is not None else []

    cfg = _sol.settings(app)
    eligible = _eligible_live_buy_users(app, event, cfg)
    if not eligible:
        return _PREV_PROCESS(app, event)

    probe_sol = max((allocation for _, allocation in eligible), default=Decimal(0))
    check = evaluate_live_pool_risk(app, event, cfg, probe_sol=probe_sol)
    if _severity(check) > 0:
        reason = f"{check['reason_code']}: {check['reason']}"
        print(
            "[solana-pool-risk] decision=%s code=%s mint=%s reason=%s"
            % (check["decision"], check["reason_code"], str(event.get("mint") or ""), check["reason"][:240])
        )
        return [
            {
                "telegram_id": tid,
                "action": "REJECT",
                "reason": reason,
                "pool_risk_code": check["reason_code"],
            }
            for tid, _ in eligible
        ]

    print(
        "[solana-pool-risk] decision=PASS code=POOL_RISK_PASS mint=%s probe_sol=%s"
        % (str(event.get("mint") or ""), check.get("evidence", {}).get("reference_probe_sol", ""))
    )
    return _PREV_PROCESS(app, event)


def install() -> None:
    global _PREV_PROCESS
    if getattr(_live, "_pool_risk_check_installed", False):
        return
    _PREV_PROCESS = _live.process_leader_event
    _live.process_leader_event = process_leader_event_with_pool_risk
    _live._pool_risk_check_installed = True
    print(
        "[solana-pool-risk] installed=true live_only=true shadow_external_outage_unaffected=true "
        "reference_probe_sol=0.01 reference_impact_hard_cap_bps=200 rugcheck=true dexscreener=true"
    )
