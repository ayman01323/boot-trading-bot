from __future__ import annotations

import csv
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from web3 import Web3

from .config import load_kv_scoped

HEADERS = [
    "updated_epoch", "chain_id", "chain_slug", "symbol", "address",
    "risk_level", "category", "source", "configured", "enabled",
    "v2_pool_count", "v3_pool_count", "pool_count", "wrapped_pool_count",
    "simulation_passes", "simulation_failures", "first_seen_epoch", "last_seen_epoch",
    "quarantine_until_epoch", "auto_scan", "auto_trade", "reason",
]

DEFAULT_STABLES = {"USDC", "USDT", "DAI", "USDBC", "FDUSD", "TUSD"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _bool(v, default=False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _atomic_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows([{h: r.get(h, "") for h in HEADERS} for r in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _token_key(chain_id, address: str) -> tuple[str, str]:
    return str(chain_id), str(address or "").strip().lower()


def _path_tokens(text: str) -> list[str]:
    out = []
    for x in str(text or "").split(">"):
        x = x.strip()
        if Web3.is_address(x):
            a = Web3.to_checksum_address(x)
            if a.lower() not in {z.lower() for z in out}:
                out.append(a)
    return out


def _active_quarantine(csv_dir: Path, now: int) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for r in _rows(Path(csv_dir) / "auto" / "execution_quarantine.csv"):
        if str(r.get("kind") or "").upper() != "TOKEN_BLOCK":
            continue
        token = str(r.get("token") or "").strip()
        if not Web3.is_address(token):
            continue
        expiry = _int(r.get("expires_at_epoch"), 0)
        if expiry <= now:
            continue
        k = _token_key(r.get("chain_id"), token)
        out[k] = max(out.get(k, 0), expiry)
    return out


def _simulation_stats(csv_dir: Path, now: int, lookback: int = 21600) -> dict[tuple[str, str], dict[str, int]]:
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    rows = _rows(Path(csv_dir) / "auto" / "auto_trade_simulations.csv")[-10000:]
    for r in rows:
        ts = _int(r.get("timestamp_epoch"), 0)
        if ts and ts < now - lookback:
            continue
        cid = str(r.get("chain_id") or "")
        ok = _bool(r.get("simulation_ok"), False)
        for token in _path_tokens(r.get("route_path") or ""):
            k = _token_key(cid, token)
            out[k]["pass" if ok else "fail"] += 1
    return out


def _stable_symbols(settings: dict) -> set[str]:
    raw = str(settings.get("product_stable_symbols") or "USDC|USDT|DAI|USDBC|FDUSD|TUSD")
    vals = {x.strip().upper() for x in raw.replace(",", "|").split("|") if x.strip()}
    return vals or set(DEFAULT_STABLES)


def refresh_product_universe(app, contexts: Iterable, now: int | None = None) -> list[dict]:
    """Build a dynamic, fail-closed AUTO product universe from local verified registries.

    This function intentionally does not make RPC calls.  Pool discovery/quoting remain the
    source of execution truth.  The universe is a prioritisation and safety layer only.
    """
    now = int(now or time.time())
    csv_dir = Path(app.csv_dir)
    settings = load_kv_scoped(csv_dir / "auto_trading_settings.csv", 0)
    if not _bool(settings.get("product_universe_enabled", "true"), True):
        return _rows(csv_dir / "auto" / "product_universe.csv")

    ctx_by_id = {str(c.config.chain_id): c for c in contexts}
    configured: dict[tuple[str, str], dict] = {}
    for r in _rows(csv_dir / "tokens.csv"):
        cid = str(r.get("chain_id") or "")
        addr = str(r.get("address") or "").strip()
        if cid not in ctx_by_id or not Web3.is_address(addr):
            continue
        a = Web3.to_checksum_address(addr)
        configured[_token_key(cid, a)] = {
            "symbol": str(r.get("symbol") or "").strip().upper(),
            "role": str(r.get("role") or "").strip().lower(),
            "enabled": _bool(r.get("enabled"), True),
        }

    # Preserve first-seen history across refreshes.
    old = {_token_key(r.get("chain_id"), r.get("address")): r for r in _rows(csv_dir / "auto" / "product_universe.csv") if Web3.is_address(str(r.get("address") or ""))}

    stats: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "v2": set(), "v3": set(), "wrapped": set(), "first": 0, "last": 0,
    })

    def touch(cid: str, addr: str, pool_id: str, kind: str, first: int, last: int, wrapped_peer: bool):
        if cid not in ctx_by_id or not Web3.is_address(addr):
            return
        a = Web3.to_checksum_address(addr)
        s = stats[_token_key(cid, a)]
        s[kind].add(pool_id)
        if wrapped_peer:
            s["wrapped"].add(pool_id)
        if first > 0:
            s["first"] = first if not s["first"] else min(s["first"], first)
        if last > 0:
            s["last"] = max(s["last"], last)

    for r in _rows(csv_dir / "auto" / "pool_registry.csv"):
        cid = str(r.get("chain_id") or "")
        ctx = ctx_by_id.get(cid)
        if not ctx:
            continue
        a, b = str(r.get("token0") or ""), str(r.get("token1") or "")
        if not (Web3.is_address(a) and Web3.is_address(b)):
            continue
        aa, bb = Web3.to_checksum_address(a), Web3.to_checksum_address(b)
        wrapped = str(ctx.config.wrapped_base_address).lower()
        pid = str(r.get("pair_address") or f"v2:{aa}:{bb}").lower()
        first = _int(r.get("first_seen_epoch"), 0)
        last = _int(r.get("last_seen_epoch"), first or now)
        touch(cid, aa, pid, "v2", first, last, bb.lower() == wrapped)
        touch(cid, bb, pid, "v2", first, last, aa.lower() == wrapped)

    for r in _rows(csv_dir / "auto" / "v3_pool_registry.csv"):
        cid = str(r.get("chain_id") or "")
        ctx = ctx_by_id.get(cid)
        if not ctx:
            continue
        a, b = str(r.get("token0") or ""), str(r.get("token1") or "")
        if not (Web3.is_address(a) and Web3.is_address(b)):
            continue
        aa, bb = Web3.to_checksum_address(a), Web3.to_checksum_address(b)
        wrapped = str(ctx.config.wrapped_base_address).lower()
        pid = str(r.get("pool_address") or f"v3:{aa}:{bb}:{r.get('fee')}").lower()
        last = _int(r.get("last_seen_epoch"), now)
        touch(cid, aa, pid, "v3", last, last, bb.lower() == wrapped)
        touch(cid, bb, pid, "v3", last, last, aa.lower() == wrapped)

    # Configured tokens are always represented even before a pool is discovered.
    for k in configured:
        stats[k]

    q = _active_quarantine(csv_dir, now)
    sims = _simulation_stats(csv_dir, now, max(300, min(86400, _int(settings.get("product_simulation_history_seconds"), 21600))))
    stables = _stable_symbols(settings)
    new_shadow = max(0, min(86400, _int(settings.get("product_new_token_shadow_seconds"), 900)))
    established_age = max(0, min(2592000, _int(settings.get("product_established_age_seconds"), 3600)))
    established_pools = max(2, min(50, _int(settings.get("product_established_min_pools"), 3)))
    strict_pools = max(2, min(20, _int(settings.get("product_strict_min_pools"), 2)))
    max_rows_chain = max(20, min(10000, _int(settings.get("product_universe_max_rows_per_chain"), 2000)))

    rows: list[dict] = []
    for (cid, alower), s in stats.items():
        ctx = ctx_by_id.get(cid)
        if not ctx:
            continue
        address = Web3.to_checksum_address(alower)
        conf = configured.get((cid, alower), {})
        symbol = str(conf.get("symbol") or "").upper()
        role = str(conf.get("role") or "")
        enabled = bool(conf.get("enabled", True)) if conf else True
        oldrow = old.get((cid, alower), {})
        first = s["first"] or _int(oldrow.get("first_seen_epoch"), 0) or s["last"] or now
        last = s["last"] or _int(oldrow.get("last_seen_epoch"), 0) or now
        v2n, v3n = len(s["v2"]), len(s["v3"])
        pooln = len(s["v2"] | s["v3"])
        wrappedn = len(s["wrapped"])
        sim = sims.get((cid, alower), {"pass": 0, "fail": 0})
        quarantine_until = q.get((cid, alower), 0)
        wrapped_addr = str(ctx.config.wrapped_base_address).lower()
        configured_flag = bool(conf)

        risk = 3
        category = "DISCOVERED_SCAN_ONLY"
        auto_scan = True
        auto_trade = False
        reason = "discovered pool token; insufficient evidence for AUTO"

        if quarantine_until > now:
            risk, category, auto_scan, auto_trade = 4, "QUARANTINE", False, False
            reason = f"execution quarantine active until {quarantine_until}"
        elif alower == wrapped_addr or role == "wrapped_base":
            risk, category, auto_scan, auto_trade = 1, "CORE_WRAPPED", enabled, enabled
            reason = "wrapped native core asset"
        elif symbol in stables:
            risk, category, auto_scan, auto_trade = 1, "CORE_STABLE", enabled, enabled
            reason = "configured core stablecoin"
        elif configured_flag and enabled:
            risk, category, auto_scan, auto_trade = 2, "CONFIGURED_LIQUID", True, True
            reason = f"operator-configured token role={role or 'token'}"
        else:
            age = max(0, now - first)
            if pooln >= established_pools and wrappedn >= 1 and age >= established_age:
                risk, category, auto_scan, auto_trade = 2, "DISCOVERED_ESTABLISHED", True, True
                reason = f"{pooln} verified pools incl. wrapped-base connection; age={age}s"
            elif age < new_shadow:
                risk, category, auto_scan, auto_trade = 3, "NEW_TOKEN_SHADOW", True, False
                reason = f"new token shadow period ({age}s < {new_shadow}s)"
            elif pooln >= strict_pools and wrappedn >= 1:
                risk, category, auto_scan, auto_trade = 3, "DISCOVERED_STRICT_AUTO", True, True
                reason = f"strict AUTO: {pooln} verified pools + wrapped-base connection; wallet simulation mandatory"
            else:
                risk, category, auto_scan, auto_trade = 3, "DISCOVERED_SCAN_ONLY", True, False
                reason = f"only {pooln} verified pool(s), wrapped connections={wrappedn}"

        # Repeated recent failures without a pass do not create a permanent blacklist,
        # but they demote an otherwise dynamic token to scan-only until evidence improves.
        if not configured_flag and risk in {2, 3} and sim["fail"] >= 5 and sim["pass"] == 0:
            risk = max(risk, 3)
            category = "SIMULATION_REVIEW"
            auto_trade = False
            reason = f"{sim['fail']} recent wallet simulation failures and no pass"

        rows.append({
            "updated_epoch": now, "chain_id": cid, "chain_slug": ctx.config.slug,
            "symbol": symbol or _short_symbol(address), "address": address,
            "risk_level": risk, "category": category,
            "source": "CONFIG+POOLS" if configured_flag and pooln else "CONFIG" if configured_flag else "DISCOVERY",
            "configured": str(configured_flag).lower(), "enabled": str(enabled).lower(),
            "v2_pool_count": v2n, "v3_pool_count": v3n, "pool_count": pooln,
            "wrapped_pool_count": wrappedn, "simulation_passes": sim["pass"], "simulation_failures": sim["fail"],
            "first_seen_epoch": first, "last_seen_epoch": last, "quarantine_until_epoch": quarantine_until,
            "auto_scan": str(bool(auto_scan)).lower(), "auto_trade": str(bool(auto_trade)).lower(), "reason": reason,
        })

    # Keep the strongest/recent products per chain while preserving all configured rows.
    by_chain: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_chain[str(r["chain_id"])].append(r)
    final: list[dict] = []
    for cid, rr in by_chain.items():
        rr.sort(key=lambda r: (
            0 if _bool(r.get("configured"), False) else 1,
            _int(r.get("risk_level"), 9),
            0 if _bool(r.get("auto_trade"), False) else 1,
            -_int(r.get("wrapped_pool_count"), 0),
            -_int(r.get("pool_count"), 0),
            -_int(r.get("simulation_passes"), 0),
            -_int(r.get("last_seen_epoch"), 0),
        ))
        final.extend(rr[:max_rows_chain])

    _atomic_rows(csv_dir / "auto" / "product_universe.csv", final)
    return final


def _short_symbol(address: str) -> str:
    a = str(address or "")
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def product_rows(csv_dir: Path, chain_id=None) -> list[dict]:
    rows = _rows(Path(csv_dir) / "auto" / "product_universe.csv")
    if chain_id is not None:
        rows = [r for r in rows if str(r.get("chain_id")) == str(chain_id)]
    return rows


def allowed_product_addresses(csv_dir: Path, chain_id, *, include_shadow=False, max_tokens=None) -> list[str]:
    settings = load_kv_scoped(Path(csv_dir) / "auto_trading_settings.csv", int(chain_id) if str(chain_id).isdigit() else 0)
    if max_tokens is None:
        max_tokens = max(4, min(250, _int(settings.get("product_max_scan_tokens_per_chain"), 60)))
    rows = product_rows(csv_dir, chain_id)
    candidates = []
    for r in rows:
        if not Web3.is_address(str(r.get("address") or "")):
            continue
        if not _bool(r.get("auto_scan"), False):
            continue
        if not include_shadow and not _bool(r.get("auto_trade"), False):
            continue
        candidates.append(r)
    candidates.sort(key=lambda r: (
        _int(r.get("risk_level"), 9),
        0 if _bool(r.get("configured"), False) else 1,
        -_int(r.get("wrapped_pool_count"), 0),
        -_int(r.get("pool_count"), 0),
        -_int(r.get("simulation_passes"), 0),
        -_int(r.get("last_seen_epoch"), 0),
    ))
    out = []
    for r in candidates:
        a = Web3.to_checksum_address(r["address"])
        if a.lower() not in {x.lower() for x in out}:
            out.append(a)
        if len(out) >= int(max_tokens):
            break
    return out


def route_product_policy(csv_dir: Path, chain_id, path: list[str]) -> dict:
    """Return fail-closed AUTO policy for intermediate assets in a route.

    On first boot before product_universe.csv exists, configured enabled tokens remain
    usable so the upgrade cannot accidentally deadlock its own discovery bootstrap.
    """
    prows = product_rows(csv_dir, chain_id)
    if not prows:
        for r in _rows(Path(csv_dir) / "tokens.csv"):
            if str(r.get("chain_id") or "") != str(chain_id) or not _bool(r.get("enabled"), True):
                continue
            addr = str(r.get("address") or "").strip()
            if not Web3.is_address(addr):
                continue
            prows.append({"address": Web3.to_checksum_address(addr), "risk_level": "2", "category": "CONFIG_BOOTSTRAP", "auto_trade": "true"})
    rows = {str(r.get("address") or "").lower(): r for r in prows}
    middle = [x for x in path[1:-1] if Web3.is_address(str(x or ""))]
    if not middle:
        return {"auto_trade": False, "risk_level": 9, "reason": "route has no intermediate products", "categories": []}
    max_risk = 1
    cats = []
    for token in middle:
        r = rows.get(str(token).lower())
        if not r:
            return {"auto_trade": False, "risk_level": 9, "reason": f"product universe missing:{token}", "categories": cats}
        risk = _int(r.get("risk_level"), 9)
        max_risk = max(max_risk, risk)
        cats.append(str(r.get("category") or ""))
        if not _bool(r.get("auto_trade"), False):
            return {"auto_trade": False, "risk_level": max_risk, "reason": f"product not AUTO-approved:{token}:{r.get('category')}", "categories": cats}
    return {"auto_trade": True, "risk_level": max_risk, "reason": "product policy passed", "categories": cats}


def universe_summary(csv_dir: Path) -> dict:
    rows = product_rows(csv_dir)
    counts = defaultdict(int)
    for r in rows:
        counts[f"L{_int(r.get('risk_level'), 9)}"] += 1
        if _bool(r.get("auto_scan"), False):
            counts["scan"] += 1
        if _bool(r.get("auto_trade"), False):
            counts["trade"] += 1
    return {"total": len(rows), **dict(counts)}
