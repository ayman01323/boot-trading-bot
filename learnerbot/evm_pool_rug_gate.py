from __future__ import annotations

import os
import threading
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as urlquote

import requests

from . import live_executor as _live

# Every EVM chain with an enabled LiveTrader router in live_executor.py.
SUPPORTED_CHAINS = {
    1: "ethereum",
    56: "bsc",
    137: "polygon",
    8453: "base",
    42161: "arbitrum",
}

_GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
_DEX_URL = "https://api.dexscreener.com/token-pairs/v1/{chain_slug}/{token}"

# Hard ceilings: settings may make these stricter, never looser.
_MIN_LIQUIDITY_USD = Decimal("1000")
_MIN_LP_LOCKED_PCT = Decimal("50")
_MAX_TOKEN_TAX = Decimal("0.20")
_MAX_ROUNDTRIP_LOSS_BPS = Decimal("1500")
_MIN_NEW_POOL_COOLING_SECONDS = 900
_MAX_VOLUME_LIQ_RATIO = Decimal("50")
_MAX_CROSS_POOL_PRICE_RATIO = Decimal("5")
_MIN_LIQUIDITY_RETAINED_PCT = Decimal("30")

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, int, str], tuple[float, object]] = {}
_LIQ_HISTORY: dict[tuple[int, str], list[tuple[float, Decimal]]] = {}
_INSTALLED = False
_ORIG_BUY = None
_ORIG_PREBROADCAST_CYCLE = None
_ORIG_PREBROADCAST_V3_CYCLE = None


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _flag(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _decision(kind: str, code: str, reason: str, evidence: dict | None = None) -> dict:
    return {
        "decision": str(kind),
        "reason_code": str(code),
        "reason": str(reason),
        "evidence": dict(evidence or {}),
    }


def _setting(trader, key: str, default: str) -> str:
    return str((getattr(trader, "settings", {}) or {}).get(key, default))


def _bounded_decimal(trader, key: str, default: str, *, floor: Decimal, hard_ceiling: Decimal) -> Decimal:
    value = _d(_setting(trader, key, default), default)
    return min(hard_ceiling, max(floor, value))


def _timeout(trader) -> float:
    return float(min(Decimal("3"), max(Decimal("0.5"), _d(_setting(trader, "live_pool_external_timeout_seconds", "2.5"), "2.5"))))


def _cache_get(provider: str, chain_id: int, token: str, ttl: float):
    now = time.monotonic()
    key = (provider, int(chain_id), str(token).lower())
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] <= ttl:
            return item[1]
    return None


def _cache_put(provider: str, chain_id: int, token: str, value) -> None:
    key = (provider, int(chain_id), str(token).lower())
    with _LOCK:
        _CACHE[key] = (time.monotonic(), value)
        if len(_CACHE) > 1024:
            cutoff = time.monotonic() - 3600
            for old_key, item in list(_CACHE.items()):
                if item[0] < cutoff:
                    _CACHE.pop(old_key, None)


def _get_json(provider: str, chain_id: int, token: str, url: str, ttl: float, timeout: float, *, params=None, headers=None):
    cached = _cache_get(provider, chain_id, token, ttl)
    if cached is not None:
        return cached, True
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    value = response.json()
    _cache_put(provider, chain_id, token, value)
    return value, False


def _locked_lp_pct(report: dict) -> Decimal | None:
    holders = report.get("lp_holders")
    if not isinstance(holders, list) or not holders:
        return None
    total = Decimal(0)
    seen = False
    for holder in holders:
        if not isinstance(holder, dict) or not _flag(holder.get("is_locked")):
            continue
        pct = _d(holder.get("percent"), "-1")
        if pct >= 0:
            seen = True
            total += pct * Decimal(100)
    return max(Decimal(0), min(Decimal(100), total)) if seen else Decimal(0)


def evaluate_goplus(report: dict) -> dict:
    if not isinstance(report, dict) or not report:
        return _decision("HARD_BLOCK", "TOKEN_SECURITY_UNAVAILABLE", "GoPlus returned no token-security record")

    trusted = _flag(report.get("trust_list"))
    evidence = {
        "trust_list": trusted,
        "is_in_dex": str(report.get("is_in_dex") or ""),
        "is_honeypot": str(report.get("is_honeypot") or ""),
        "cannot_buy": str(report.get("cannot_buy") or ""),
        "cannot_sell_all": str(report.get("cannot_sell_all") or ""),
        "buy_tax": str(report.get("buy_tax") or ""),
        "sell_tax": str(report.get("sell_tax") or ""),
        "lp_locked_pct": _locked_lp_pct(report),
    }

    if _flag(report.get("is_honeypot")):
        return _decision("HARD_BLOCK", "HONEYPOT", "GoPlus identifies the token as a honeypot", evidence)
    if _flag(report.get("cannot_buy")):
        return _decision("HARD_BLOCK", "TOKEN_CANNOT_BUY", "GoPlus reports that the token cannot be bought normally", evidence)
    if _flag(report.get("cannot_sell_all")):
        return _decision("HARD_BLOCK", "TOKEN_CANNOT_SELL_ALL", "GoPlus reports a restriction on selling the full token balance", evidence)
    if str(report.get("is_in_dex") or "") == "0":
        return _decision("HARD_BLOCK", "TOKEN_NOT_IN_DEX", "GoPlus reports no recognised DEX market for this token", evidence)

    buy_tax = _d(report.get("buy_tax"), 0)
    sell_tax = _d(report.get("sell_tax"), 0)
    if buy_tax >= _MAX_TOKEN_TAX or sell_tax >= _MAX_TOKEN_TAX:
        return _decision(
            "HARD_BLOCK", "EXCESSIVE_TOKEN_TAX",
            f"token tax is too high for LIVE safety (buy={buy_tax * 100:.2f}%, sell={sell_tax * 100:.2f}%, hard ceiling={_MAX_TOKEN_TAX * 100:.2f}%)",
            evidence,
        )

    # Famous/trusted tokens can legitimately expose issuer controls (for example
    # regulated stablecoins). Explicit honeypot/sellability/tax checks above still
    # apply, but ownership-feature heuristics below are for untrusted contracts.
    if not trusted:
        dangerous = {
            "transfer_pausable": "owner can pause transfers/trading",
            "owner_change_balance": "owner can change holder balances",
            "selfdestruct": "contract can self-destruct",
            "hidden_owner": "contract has a hidden owner",
            "can_take_back_ownership": "ownership can be reclaimed",
            "is_blacklisted": "contract exposes blacklist risk",
        }
        for field, reason in dangerous.items():
            if _flag(report.get(field)):
                evidence["blocking_field"] = field
                return _decision("HARD_BLOCK", "TOKEN_CONTROL_RISK", f"GoPlus severe token-control risk: {reason}", evidence)
        if str(report.get("is_open_source") or "") == "0":
            return _decision("HARD_BLOCK", "TOKEN_NOT_OPEN_SOURCE", "untrusted token contract is not open source; LIVE fails closed", evidence)

    return _decision("PASS", "TOKEN_SECURITY_PASS", "GoPlus token-security screen passed", evidence)


def _pair_liquidity_usd(pair: dict) -> Decimal:
    return max(Decimal(0), _d((pair.get("liquidity") or {}).get("usd"), 0))


def _pair_price(pair: dict) -> Decimal:
    return max(Decimal(0), _d(pair.get("priceUsd"), 0))


def _pair_age_seconds(pair: dict, now_epoch: float) -> float | None:
    try:
        raw = float(pair.get("pairCreatedAt") or 0)
    except Exception:
        return None
    if raw <= 0:
        return None
    created = raw / 1000 if raw > 10_000_000_000 else raw
    return max(0.0, float(now_epoch) - created)


def _prior_liquidity(chain_id: int, token: str, now_epoch: float, current: Decimal) -> Decimal | None:
    key = (int(chain_id), str(token).lower())
    with _LOCK:
        history = _LIQ_HISTORY.setdefault(key, [])
        prior = next((value for ts, value in reversed(history) if now_epoch - ts <= 3600 and value > 0), None)
        history.append((now_epoch, current))
        _LIQ_HISTORY[key] = [(ts, value) for ts, value in history if now_epoch - ts <= 3600][-120:]
        return prior


def evaluate_dexscreener(pairs, *, chain_id: int, token: str, now_epoch: float | None = None, min_liquidity_usd: Decimal = _MIN_LIQUIDITY_USD) -> dict:
    now_epoch = float(time.time() if now_epoch is None else now_epoch)
    if not isinstance(pairs, list):
        return _decision("HARD_BLOCK", "DEX_DATA_INVALID", "DexScreener returned invalid pool data")
    chain_slug = SUPPORTED_CHAINS.get(int(chain_id))
    if not chain_slug:
        return _decision("HARD_BLOCK", "UNSUPPORTED_CHAIN", f"no pool-rug policy is defined for EVM chain {chain_id}")
    clean = [p for p in pairs if isinstance(p, dict) and str(p.get("chainId") or "").lower() == chain_slug]
    if not clean:
        return _decision("HARD_BLOCK", "NO_INDEXED_POOL", "no indexed pool exists for this token on the target chain")

    total_liq = sum((_pair_liquidity_usd(p) for p in clean), Decimal(0))
    max_liq = max((_pair_liquidity_usd(p) for p in clean), default=Decimal(0))
    volume = sum((max(Decimal(0), _d((p.get("volume") or {}).get("h24"), 0)) for p in clean), Decimal(0))
    absolute_floor = max(Decimal("100"), min_liquidity_usd / Decimal(10))
    material_floor = max(absolute_floor, max_liq * Decimal("0.10")) if max_liq > 0 else absolute_floor
    material = [p for p in clean if _pair_liquidity_usd(p) >= material_floor]
    if not material:
        material = [max(clean, key=_pair_liquidity_usd)]

    ages = [v for v in (_pair_age_seconds(p, now_epoch) for p in material) if v is not None]
    youngest = min(ages) if ages else None
    prices = [_pair_price(p) for p in material if _pair_price(p) > 0]
    cross_ratio = max(prices) / min(prices) if len(prices) >= 2 and min(prices) > 0 else Decimal(1)
    volume_ratio = volume / max(Decimal(1), total_liq)
    evidence = {
        "dex_pair_count": len(clean),
        "dex_material_pair_count": len(material),
        "dex_ids": sorted({str(p.get("dexId") or "unknown") for p in material}),
        "dex_liquidity_usd_total": total_liq,
        "dex_liquidity_usd_max_pair": max_liq,
        "dex_volume_h24_usd": volume,
        "dex_volume_liquidity_ratio": volume_ratio,
        "dex_youngest_material_pair_age_seconds": youngest,
        "dex_cross_pool_price_ratio": cross_ratio,
    }

    if total_liq < min_liquidity_usd:
        return _decision(
            "HARD_BLOCK", "POOL_LIQUIDITY_TOO_LOW",
            f"indexed pool liquidity ${total_liq:.2f} is below LIVE minimum ${min_liquidity_usd:.2f}", evidence,
        )

    prior = _prior_liquidity(chain_id, token, now_epoch, total_liq)
    if prior and prior > 0:
        retained = total_liq * Decimal(100) / prior
        evidence["dex_prior_liquidity_usd_within_1h"] = prior
        evidence["dex_liquidity_retained_pct"] = retained
        if retained < _MIN_LIQUIDITY_RETAINED_PCT:
            return _decision(
                "HARD_BLOCK", "POOL_LIQUIDITY_COLLAPSE",
                f"pool liquidity retained only {retained:.2f}% of a prior <=1h snapshot", evidence,
            )

    if youngest is not None and youngest < _MIN_NEW_POOL_COOLING_SECONDS:
        return _decision(
            "HARD_BLOCK", "POOL_NEW_COOLING",
            f"material pool age {int(youngest)}s is inside the {_MIN_NEW_POOL_COOLING_SECONDS}s LIVE cooling period", evidence,
        )
    if youngest is not None and youngest < 3600 and volume_ratio > _MAX_VOLUME_LIQ_RATIO:
        return _decision(
            "HARD_BLOCK", "WASH_VOLUME_RISK",
            f"fresh-pool 24h volume/liquidity ratio {volume_ratio:.2f} exceeds {_MAX_VOLUME_LIQ_RATIO}", evidence,
        )
    if youngest is not None and youngest < 3600 and cross_ratio > _MAX_CROSS_POOL_PRICE_RATIO:
        return _decision(
            "HARD_BLOCK", "CROSS_POOL_PRICE_DISCONTINUITY",
            f"fresh material pools disagree by {cross_ratio:.2f}x on USD price", evidence,
        )
    return _decision("PASS", "DEX_POOL_PASS", "DexScreener pool-state screen passed", evidence)


def _fetch_goplus(trader, token: str) -> tuple[dict, bool]:
    chain_id = int(trader.chain.chain_id)
    timeout = _timeout(trader)
    ttl = float(max(60, _int(_setting(trader, "live_pool_goplus_cache_seconds", "900"), 900)))
    headers = {"Accept": "application/json", "User-Agent": "boot-trading-bot/evm-pool-rug-gate"}
    access_token = os.environ.get("GOPLUS_ACCESS_TOKEN", "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    payload, cached = _get_json(
        "goplus", chain_id, token, _GOPLUS_URL.format(chain_id=chain_id), ttl, timeout,
        params={"contract_addresses": str(token)}, headers=headers,
    )
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError("GoPlus response has no result object")
    report = result.get(str(token).lower()) or result.get(str(token))
    if not isinstance(report, dict):
        raise ValueError("GoPlus response has no record for token")
    return report, cached


def _fetch_dexscreener(trader, token: str) -> tuple[list, bool]:
    chain_id = int(trader.chain.chain_id)
    chain_slug = SUPPORTED_CHAINS[chain_id]
    ttl = float(max(15, _int(_setting(trader, "live_pool_dex_cache_seconds", "60"), 60)))
    payload, cached = _get_json(
        "dexscreener", chain_id, token,
        _DEX_URL.format(chain_slug=urlquote(chain_slug, safe=""), token=urlquote(str(token), safe="")),
        ttl, _timeout(trader),
        headers={"Accept": "application/json", "User-Agent": "boot-trading-bot/evm-pool-rug-gate"},
    )
    if not isinstance(payload, list):
        raise ValueError("DexScreener response is not a token-pairs list")
    return payload, cached


def external_pool_rug_check(trader, token: str) -> dict:
    chain_id = int(trader.chain.chain_id)
    if chain_id not in SUPPORTED_CHAINS:
        return _decision("HARD_BLOCK", "UNSUPPORTED_CHAIN", f"no pool-rug policy is defined for EVM chain {chain_id}")
    if str(token).lower() == str(trader.wrapped).lower():
        return _decision("PASS", "WRAPPED_NATIVE_PASS", "wrapped native asset is the route anchor")

    try:
        report, gp_cached = _fetch_goplus(trader, token)
    except Exception as exc:
        return _decision(
            "HARD_BLOCK", "GOPLUS_UNAVAILABLE",
            f"GoPlus token-security check unavailable ({type(exc).__name__}); LIVE fails closed",
            {"goplus_available": False},
        )
    security = evaluate_goplus(report)
    security["evidence"]["goplus_cached"] = bool(gp_cached)
    if security["decision"] != "PASS":
        return security

    try:
        pairs, dex_cached = _fetch_dexscreener(trader, token)
    except Exception as exc:
        return _decision(
            "HARD_BLOCK", "DEXSCREENER_UNAVAILABLE",
            f"DexScreener pool check unavailable ({type(exc).__name__}); LIVE fails closed",
            {**security.get("evidence", {}), "dexscreener_available": False},
        )

    min_liquidity = _bounded_decimal(
        trader, "live_pool_min_liquidity_usd", str(_MIN_LIQUIDITY_USD),
        floor=_MIN_LIQUIDITY_USD, hard_ceiling=Decimal("1000000000"),
    )
    pool = evaluate_dexscreener(pairs, chain_id=chain_id, token=token, min_liquidity_usd=min_liquidity)
    pool["evidence"].update(security.get("evidence", {}))
    pool["evidence"]["dexscreener_cached"] = bool(dex_cached)
    if pool["decision"] != "PASS":
        return pool

    # A young pool with mostly-unlocked LP is especially exposed to a classic LP
    # pull. Established pools are not rejected solely because concentrated/V3 LP
    # positions are not represented as locked ERC-20 LP tokens.
    lp_locked = security.get("evidence", {}).get("lp_locked_pct")
    youngest = pool.get("evidence", {}).get("dex_youngest_material_pair_age_seconds")
    if lp_locked is not None and youngest is not None and youngest < 86400:
        lp_floor = _bounded_decimal(
            trader, "live_pool_min_lp_locked_pct", str(_MIN_LP_LOCKED_PCT),
            floor=_MIN_LP_LOCKED_PCT, hard_ceiling=Decimal(100),
        )
        if _d(lp_locked, 0) < lp_floor:
            return _decision(
                "HARD_BLOCK", "LP_LOCK_RISK",
                f"young pool has only {_d(lp_locked, 0):.2f}% observed locked LP; LIVE minimum is {lp_floor:.2f}%",
                pool.get("evidence", {}),
            )

    return _decision("PASS", "POOL_RUG_PASS", "EVM token and pool rug-safety checks passed", pool.get("evidence", {}))


def _raise_if_blocked(result: dict) -> None:
    if str(result.get("decision") or "HARD_BLOCK") != "PASS":
        raise _live.LiveTradingError(
            f"Pool-rug safety block [{result.get('reason_code') or 'UNKNOWN'}]: {result.get('reason') or 'unsafe pool/token'}"
        )


def _route_tokens(trader, path) -> list[str]:
    out = []
    wrapped = str(trader.wrapped).lower()
    for token in list(path or []):
        value = str(token)
        if value.lower() == wrapped or value.lower() in {x.lower() for x in out}:
            continue
        out.append(value)
    return out


def check_live_route(trader, path) -> dict:
    checked = []
    for token in _route_tokens(trader, path):
        result = external_pool_rug_check(trader, token)
        checked.append({"token": token, "reason_code": result.get("reason_code"), "decision": result.get("decision")})
        _raise_if_blocked(result)
    return {"decision": "PASS", "reason_code": "ROUTE_POOL_RUG_PASS", "checked": checked}


def _manual_roundtrip_check(trader, token: str, amount_native, expected_token_raw: int) -> dict:
    amount_wei = int(_d(amount_native, 0) * Decimal(10**18))
    if amount_wei <= 0 or int(expected_token_raw) <= 0:
        return _decision("HARD_BLOCK", "REFERENCE_DEPTH_INVALID", "cannot establish a positive reverse-exit reference amount")
    try:
        back = int(trader.router.functions.getAmountsOut(int(expected_token_raw), [token, trader.wrapped]).call()[-1])
    except Exception as exc:
        return _decision("HARD_BLOCK", "NO_REVERSE_EXIT_ROUTE", f"reverse-exit quote failed ({type(exc).__name__})")
    loss_bps = max(Decimal(0), (Decimal(1) - Decimal(back) / Decimal(amount_wei)) * Decimal(10000))
    limit = _bounded_decimal(
        trader, "live_pool_reference_max_roundtrip_loss_bps", "1000",
        floor=Decimal("100"), hard_ceiling=_MAX_ROUNDTRIP_LOSS_BPS,
    )
    evidence = {"reference_roundtrip_loss_bps": loss_bps, "reference_roundtrip_limit_bps": limit}
    if loss_bps > limit:
        return _decision(
            "HARD_BLOCK", "THIN_REFERENCE_DEPTH",
            f"reverse-exit reference loses {loss_bps:.1f} bps, above LIVE pool-rug ceiling {limit:.1f} bps", evidence,
        )
    return _decision("PASS", "REFERENCE_DEPTH_PASS", "reverse-exit reference depth passed", evidence)


def buy_with_pool_rug_gate(self, token: str, amount_native, confirm: str):
    # Reject before any external safety call when LIVE/confirmation itself is off.
    self._require_enabled("BUY")
    self._confirm(confirm)
    q = self.quote_buy(token, amount_native)
    external = external_pool_rug_check(self, q.token)
    _raise_if_blocked(external)
    expected_raw = int(_d(q.expected_out_human, 0) * (Decimal(10) ** int(q.token_decimals)))
    reverse = _manual_roundtrip_check(self, q.token, amount_native, expected_raw)
    _raise_if_blocked(reverse)
    return _ORIG_BUY(self, token, amount_native, confirm)


def prebroadcast_cycle_with_pool_rug_gate(self, path, amount_native, min_net_profit_native):
    check_live_route(self, path)
    return _ORIG_PREBROADCAST_CYCLE(self, path, amount_native, min_net_profit_native)


def prebroadcast_v3_cycle_with_pool_rug_gate(self, path, fees, amount_native, min_net_profit_native, router_address, quoter_address):
    check_live_route(self, path)
    return _ORIG_PREBROADCAST_V3_CYCLE(self, path, fees, amount_native, min_net_profit_native, router_address, quoter_address)


def install() -> None:
    global _INSTALLED, _ORIG_BUY, _ORIG_PREBROADCAST_CYCLE, _ORIG_PREBROADCAST_V3_CYCLE
    if _INSTALLED:
        return
    _ORIG_BUY = _live.LiveTrader.buy
    _ORIG_PREBROADCAST_CYCLE = _live.LiveTrader._prebroadcast_cycle
    _ORIG_PREBROADCAST_V3_CYCLE = _live.LiveTrader._prebroadcast_v3_cycle
    _live.LiveTrader.buy = buy_with_pool_rug_gate
    _live.LiveTrader._prebroadcast_cycle = prebroadcast_cycle_with_pool_rug_gate
    _live.LiveTrader._prebroadcast_v3_cycle = prebroadcast_v3_cycle_with_pool_rug_gate
    _live.LiveTrader._evm_pool_rug_gate_installed = True
    _INSTALLED = True
    print(
        "[evm-pool-rug] installed=true chains=ethereum,bsc,base,arbitrum,polygon "
        "goplus=true dexscreener=true liquidity_collapse=true reverse_exit=true fail_closed=true"
    )


install()
