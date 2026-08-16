from __future__ import annotations

import csv
import hashlib
import time
from decimal import Decimal
from itertools import permutations
from pathlib import Path

from web3 import Web3

from .config import load_kv_scoped
from .copy_engine import global_top20
from .live_executor import LiveTrader


# Official PancakeSwap V2 factory fallbacks.  The scanner first asks the
# configured router for factory(), so a CSV router override remains authoritative.
V2_FACTORY_FALLBACKS = {
    56: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",   # PancakeSwap V2 BSC
    1: "0x1097053Fd2ea711dad45caCcc45EfF7548fCB362",    # PancakeSwap V2 Ethereum
    42161: "0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E", # PancakeSwap V2 Arbitrum
    8453: "0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E",  # PancakeSwap V2 Base
    137: "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",   # QuickSwap V2 Polygon
}

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

ROUTER_FACTORY_ABI = [
    {
        "type": "function",
        "name": "factory",
        "stateMutability": "pure",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    }
]

FACTORY_ABI = [
    {
        "type": "function",
        "name": "getPair",
        "stateMutability": "view",
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "outputs": [{"name": "pair", "type": "address"}],
    }
]


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default))


def _positive_dec(v, default="0") -> Decimal:
    d = _dec(v, default)
    return d if d > 0 else Decimal(str(default))


def _route_tokens(fingerprint: str) -> list[str]:
    """Extract unique checksum token addresses in learned route order."""
    parts = (fingerprint or "").split("|")
    if len(parts) < 3:
        return []
    out: list[str] = []
    for x in parts[2].split(">"):
        x = x.strip()
        if not (x.startswith("0x") and len(x) == 42):
            continue
        try:
            a = Web3.to_checksum_address(x)
        except Exception:
            continue
        if a not in out:
            out.append(a)
    return out


def _historical_cycle_variants(tokens: list[str], wrapped: str, max_route_tokens: int) -> list[list[str]]:
    """Preserve the learned order and its reverse when they are plausible cycles."""
    wrapped = Web3.to_checksum_address(wrapped)
    if wrapped not in tokens:
        return []
    if not (3 <= len(tokens) <= max_route_tokens):
        # At least wrapped + two distinct intermediate assets for triangular arb.
        return []
    i = tokens.index(wrapped)
    rotated = tokens[i:] + tokens[:i]
    core = [a for a in rotated[1:] if a != wrapped]
    if len(core) < 2:
        return []
    variants = [[wrapped] + core + [wrapped]]
    rev = [wrapped] + list(reversed(core)) + [wrapped]
    if rev != variants[0]:
        variants.append(rev)
    return variants


def _candidate_cycles(
    tokens: list[str],
    wrapped: str,
    max_route_tokens: int,
    max_variants: int,
) -> list[list[str]]:
    """Create executable-cycle candidates from a learned token set.

    The historical path remains the first candidate, but the scanner also tries
    triangular/subset permutations.  This prevents a historical cross-DEX path
    from being blindly replayed through PancakeSwap V2 when one of its original
    V2 hops does not exist.
    """
    wrapped = Web3.to_checksum_address(wrapped)
    if wrapped not in tokens:
        return []

    core = [a for a in tokens if a != wrapped]
    max_core = min(len(core), max(0, max_route_tokens - 1))
    if max_core < 2:
        return []

    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(path: list[str]):
        key = tuple(a.lower() for a in path)
        if key not in seen and len(out) < max_variants:
            seen.add(key)
            out.append(path)

    for p in _historical_cycle_variants(tokens, wrapped, max_route_tokens):
        add(p)

    # Prefer shorter triangular routes first, then longer learned-token routes.
    for n in range(2, max_core + 1):
        for perm in permutations(core, n):
            add([wrapped, *perm, wrapped])
            if len(out) >= max_variants:
                return out
    return out


def _atomic_write(path: Path, rows: list[dict], headers: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})
        f.flush()
    tmp.replace(path)


def _active_auto_users(csv_dir: Path) -> set[str]:
    """Best-effort list of ACTIVE users who are permitted to auto trade."""
    path = Path(csv_dir) / "users.csv"
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return set()
    return {
        str(r.get("telegram_id") or "").strip()
        for r in rows
        if str(r.get("telegram_id") or "").strip()
        and str(r.get("status") or "").strip().upper() == "ACTIVE"
        and _bool(r.get("can_auto_trade"), False)
    }


def _scanner_input_base(app, chain_id: int, settings: dict) -> Decimal:
    """Choose a conservative wallet-neutral probe size.

    If scanner_input_base is explicitly configured, use it. Otherwise use the
    smallest resolved auto_input_base among ACTIVE auto-enabled users and the
    platform default. Each user is still re-quoted/simulated at their own size
    immediately before signing.
    """
    explicit = _positive_dec(settings.get("scanner_input_base"), "0")
    if explicit > 0:
        return explicit

    platform_default = _positive_dec(settings.get("auto_input_base", "0.005"), "0.005")
    candidates = [platform_default]

    path = Path(app.csv_dir) / "user_trading_settings.csv"
    if not path.exists():
        return platform_default

    active = _active_auto_users(Path(app.csv_dir))
    per_user: dict[str, dict[str, Decimal]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("setting") or "").strip() != "auto_input_base":
                    continue
                tid = str(r.get("telegram_id") or "").strip()
                if not tid or (active and tid not in active):
                    continue
                scope = str(r.get("chain_id") or "*").strip()
                if scope not in {"*", "0", str(chain_id)}:
                    continue
                value = _positive_dec(r.get("value"), "0")
                if value <= 0:
                    continue
                per_user.setdefault(tid, {})[scope] = value
    except Exception:
        return platform_default

    for scopes in per_user.values():
        resolved = scopes.get(str(chain_id)) or scopes.get("*") or scopes.get("0")
        if resolved and resolved > 0:
            candidates.append(resolved)
    return min(candidates)


def _resolve_factory(trader: LiveTrader) -> str:
    """Resolve the factory matching the configured router, with official fallback."""
    configured = (
        trader.settings.get("v2_factory_address")
        or trader.settings.get("factory_address")
        or ""
    ).strip()
    if configured:
        factory = Web3.to_checksum_address(configured)
    else:
        factory = None
        try:
            router = trader.w3.eth.contract(address=trader.router_address, abi=ROUTER_FACTORY_ABI)
            factory = Web3.to_checksum_address(router.functions.factory().call())
        except Exception:
            fallback = V2_FACTORY_FALLBACKS.get(trader.chain.chain_id)
            if fallback:
                factory = Web3.to_checksum_address(fallback)
    if not factory:
        raise RuntimeError(f"No V2 factory configured for chain {trader.chain.chain_id}")
    if not trader.w3.eth.get_code(factory):
        raise RuntimeError(f"V2 factory has no contract code: {factory}")
    return factory


def _path_pairs_exist(
    trader: LiveTrader,
    factory_contract,
    path: list[str],
    pair_cache: dict[tuple[str, str], str | None],
) -> tuple[bool, str]:
    """Validate every adjacent V2 pair before asking the router for a quote."""
    for a, b in zip(path, path[1:]):
        aa = Web3.to_checksum_address(a)
        bb = Web3.to_checksum_address(b)
        key = tuple(sorted((aa.lower(), bb.lower())))
        if key not in pair_cache:
            try:
                pair = Web3.to_checksum_address(factory_contract.functions.getPair(aa, bb).call())
                if pair.lower() == ZERO_ADDRESS.lower() or not trader.w3.eth.get_code(pair):
                    pair_cache[key] = None
                else:
                    pair_cache[key] = pair
            except Exception:
                pair_cache[key] = None
        if not pair_cache[key]:
            return False, f"missing_v2_pair:{aa}>{bb}"
    return True, "pairs_ok"


def _reject_row(now, cid, slug, wallet, behaviour, copy_score, path, stage, reason):
    return {
        "observed_at_epoch": now,
        "chain_id": cid,
        "chain_slug": slug,
        "wallet": wallet,
        "behaviour": behaviour,
        "copy_score": copy_score,
        "route_path": ">".join(path),
        "stage": stage,
        "reason": str(reason)[:400],
    }


LIVE_HEADERS = [
    "chain_id", "chain_slug", "wallet", "behaviour", "route_id", "route_path",
    "route_kind", "protocol", "router_address", "quoter_address", "route_fees",
    "venue_plan", "execution_mode", "scanner_exact", "observed_at_epoch", "quote_input_base",
    "source_input_base", "quoted_output_base", "expected_gross_profit_base",
    "estimated_gas_base", "gas_estimate_units", "builder_fee_base",
    "slippage_reserve_base", "price_impact_bps", "copy_score", "source_verified",
    "exact_quote_ok", "simulation_ok", "liquidity_ok", "sellability_ok",
    "route_approved", "whole_route_approved", "atomic_profit_protection",
    "canary_complete", "enabled", "notes",
]


def scan_live_routes(app, contexts) -> tuple[Path, list[dict]]:
    """Build fresh PancakeSwap V2 cyclic candidates from learned Top-20 routes.

    Key safety rule: historical routes are *candidate generators only*.  A route
    is written to auto/learned_route_opportunities.csv only when every current V2
    pair exists, an exact current router quote succeeds, the edge is positive
    after scanner slippage reserve, and the 2x-size liquidity/impact check passes.

    The scanner is intentionally wallet-neutral.  simulation_ok and atomic profit
    protection remain false here; the selected user's wallet is re-quoted and
    profit-protected with LiveTrader.simulate_cycle() immediately before signing.
    """
    settings = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    out_path = Path(app.csv_dir) / "auto" / "learned_route_opportunities.csv"
    reject_path = Path(app.csv_dir) / "auto" / "route_scanner_rejections.csv"

    headers = LIVE_HEADERS
    reject_headers = [
        "observed_at_epoch", "chain_id", "chain_slug", "wallet", "behaviour",
        "copy_score", "route_path", "stage", "reason",
    ]

    if not _bool(settings.get("scanner_enabled", "true"), True):
        _atomic_write(out_path, [], headers)
        _atomic_write(reject_path, [], reject_headers)
        return out_path, []

    max_routes = max(1, min(200, _int(settings.get("max_routes_per_cycle", "40"), 40)))
    max_route_tokens = max(3, min(7, _int(settings.get("max_route_tokens", "5"), 5)))
    max_impact_bps = max(1, min(5000, _int(settings.get("max_price_impact_bps", "200"), 200)))
    min_copy_score = _dec(settings.get("min_source_copy_score", "65"), "65")
    max_variants = max(2, min(120, _int(settings.get("max_variants_per_fingerprint", "48"), 48)))
    max_checks = max(max_routes, min(5000, _int(settings.get("max_candidate_checks_per_cycle", "800"), 800)))
    scanner_min_edge = max(Decimal(0), _dec(settings.get("scanner_min_edge_base", "0"), "0"))

    top = global_top20(contexts, app.csv_dir)
    by_chain = {c.config.chain_id: c for c in contexts}
    rows: list[dict] = []
    rejected: list[dict] = []
    seen: set[tuple[int, tuple[str, ...]]] = set()
    pair_caches: dict[int, dict[tuple[str, str], str | None]] = {}
    factory_contracts = {}
    candidate_checks = 0
    now = int(time.time())

    for cand in top:
        if candidate_checks >= max_checks:
            break
        cid = int(cand["chain_id"])
        score = Decimal(str(cand.get("copy_score") or 0))
        if score < min_copy_score:
            continue
        ctx = by_chain.get(cid)
        if not ctx:
            continue
        wallet = str(cand["wallet"]).lower()
        behaviour = cand.get("behaviour", "")

        fps = ctx.conn.execute(
            """SELECT route_fingerprint,COUNT(*) n,MAX(net_base) max_net
               FROM profit_evidence
               WHERE wallet=? AND proof_quality='PROVEN_WRAPPED_BASE' AND net_base>0
                 AND route_fingerprint IS NOT NULL AND route_fingerprint!=''
               GROUP BY route_fingerprint ORDER BY n DESC,max_net DESC LIMIT 12""",
            (wallet,),
        ).fetchall()
        if not fps:
            continue

        try:
            trader = LiveTrader(app, ctx.config.slug, require_wallet=False)
            if cid not in factory_contracts:
                factory_address = _resolve_factory(trader)
                factory_contracts[cid] = trader.w3.eth.contract(address=factory_address, abi=FACTORY_ABI)
                pair_caches[cid] = {}
            factory = factory_contracts[cid]
        except Exception as exc:
            rejected.append(
                _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), [], "factory", exc)
            )
            continue

        probe_input = _scanner_input_base(app, cid, settings)
        slippage_bps = trader._slippage_bps()

        for fp_row in fps:
            if candidate_checks >= max_checks:
                break
            tokens = _route_tokens(fp_row["route_fingerprint"])
            variants = _candidate_cycles(tokens, ctx.config.wrapped_base_address, max_route_tokens, max_variants)
            for path in variants:
                if candidate_checks >= max_checks:
                    break
                route_key = (cid, tuple(a.lower() for a in path))
                if route_key in seen:
                    continue
                seen.add(route_key)
                candidate_checks += 1

                pairs_ok, pair_reason = _path_pairs_exist(trader, factory, path, pair_caches[cid])
                if not pairs_ok:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "pair", pair_reason)
                    )
                    continue

                try:
                    quote = trader.cycle_quote(path, probe_input)
                    gross = quote["gross_profit"]
                    quoted_out = quote["amount_out"]
                except Exception as exc:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "quote", f"{type(exc).__name__}:{exc}")
                    )
                    continue

                if gross <= 0:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "edge", f"non_positive_gross_edge:{gross}")
                    )
                    continue

                try:
                    q2 = trader.cycle_quote(path, probe_input * 2)
                    ratio = (q2["amount_out"] / Decimal(2)) / quoted_out if quoted_out > 0 else Decimal(0)
                    impact_bps = max(Decimal(0), (Decimal(1) - ratio) * Decimal(10000))
                except Exception as exc:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "liquidity", f"2x_quote_failed:{type(exc).__name__}")
                    )
                    continue

                if impact_bps > Decimal(max_impact_bps):
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "liquidity", f"price_impact_bps:{impact_bps:.2f}>{max_impact_bps}")
                    )
                    continue

                slippage_reserve = max(Decimal(0), gross) * Decimal(slippage_bps) / Decimal(10000)
                conservative_scanner_edge = gross - slippage_reserve
                if conservative_scanner_edge <= scanner_min_edge:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "edge", f"scanner_edge_after_slippage:{conservative_scanner_edge}")
                    )
                    continue

                # Pair existence + successful cyclic quote proves a current V2 path exists.
                # It does NOT prove the user's transaction is profitable after gas or token
                # transfer behaviour; that remains a mandatory wallet-specific simulation.
                whole_route_ok = len(path) >= 4 and all(trader.w3.eth.get_code(a) for a in set(path))
                if not whole_route_ok:
                    rejected.append(
                        _reject_row(now, cid, ctx.config.slug, wallet, behaviour, str(score), path, "code", "token_contract_code_missing")
                    )
                    continue

                route_text = ">".join(path)
                route_id = hashlib.sha256(f"{cid}|{route_text}".encode()).hexdigest()[:20]
                rows.append(
                    {
                        "chain_id": cid,
                        "chain_slug": ctx.config.slug,
                        "wallet": wallet,
                        "behaviour": behaviour,
                        "route_id": route_id,
                        "route_path": route_text,
                        "router_address": trader.router_address,
                        "scanner_exact": "true",
                        "observed_at_epoch": now,
                        "quote_input_base": f"{probe_input:f}",
                        "source_input_base": f"{probe_input:f}",
                        "quoted_output_base": f"{quoted_out:f}",
                        "expected_gross_profit_base": f"{gross:f}",
                        "estimated_gas_base": "0",
                        "gas_estimate_units": 0,
                        "builder_fee_base": "0",
                        "slippage_reserve_base": f"{slippage_reserve:f}",
                        "price_impact_bps": f"{impact_bps:.2f}",
                        "copy_score": str(score),
                        "source_verified": "true",
                        "exact_quote_ok": "true",
                        "simulation_ok": "false",
                        "liquidity_ok": "true",
                        "sellability_ok": "false",
                        "route_approved": "true",
                        "whole_route_approved": "true",
                        "atomic_profit_protection": "false",
                        "canary_complete": "false",
                        # enabled means eligible for the *next wallet-specific simulation gate*,
                        # not permission to sign by itself.
                        "enabled": "true",
                        "notes": "factory_pairs_validated|wallet_specific_simulation_required",
                    }
                )

    rows.sort(
        key=lambda r: Decimal(str(r.get("expected_gross_profit_base") or 0))
        - Decimal(str(r.get("slippage_reserve_base") or 0)),
        reverse=True,
    )
    rows = rows[:max_routes]
    rejected = rejected[-500:]

    _atomic_write(out_path, rows, headers)
    _atomic_write(reject_path, rejected, reject_headers)
    return out_path, rows
