from __future__ import annotations

"""Bounded read-only RPC failover for learnerbot.route_scanner.

The historical route scanner used the base LiveTrader and treated any pair lookup
exception as a missing V2 pair.  Under provider throttling this could poison the
per-cycle pair cache and reject valid routes as structurally absent.

This patch reuses the already-audited scanner-only failover trader used by the
full-power scanner.  It never loads a signer, never changes LIVE/AUTO/ARMED, and
never relaxes quote freshness, route, profit, liquidity, slippage, simulation or
rug checks.
"""

from . import route_scanner as _route
from .full_power_scanner_rpc_failover_patch import (
    ScannerFailoverLiveTrader,
    _rpc_error_kind,
)

_BASE_RESOLVE_FACTORY = _route._resolve_factory
_BASE_SCAN_LIVE_ROUTES = _route.scan_live_routes
_MAX_SCAN_ATTEMPTS = 3
_INSTALLED = False


def _can_failover(trader) -> bool:
    return bool(
        isinstance(trader, ScannerFailoverLiveTrader)
        and getattr(trader, "_scanner_read_only", False)
        and hasattr(trader, "_scanner_failover")
    )


def resolve_factory_with_rpc_failover(trader) -> str:
    if not _can_failover(trader):
        return _BASE_RESOLVE_FACTORY(trader)

    configured = (
        trader.settings.get("v2_factory_address")
        or trader.settings.get("factory_address")
        or ""
    ).strip()
    attempted: set[int] = set()

    while True:
        try:
            if configured:
                factory = _route.Web3.to_checksum_address(configured)
            else:
                factory = None
                try:
                    router = trader.w3.eth.contract(
                        address=trader.router_address,
                        abi=_route.ROUTER_FACTORY_ABI,
                    )
                    factory = _route.Web3.to_checksum_address(
                        router.functions.factory().call()
                    )
                except Exception as exc:
                    # Provider pressure must fail over before falling back to a
                    # hard-coded factory.  A deterministic router incompatibility
                    # keeps the historical official-fallback behaviour.
                    if _rpc_error_kind(exc):
                        raise
                    fallback = _route.V2_FACTORY_FALLBACKS.get(trader.chain.chain_id)
                    if fallback:
                        factory = _route.Web3.to_checksum_address(fallback)

            if not factory:
                raise RuntimeError(
                    f"No V2 factory configured for chain {trader.chain.chain_id}"
                )
            if not trader.w3.eth.get_code(factory):
                raise RuntimeError(f"V2 factory has no contract code: {factory}")
            return factory
        except Exception as exc:
            kind = _rpc_error_kind(exc)
            if kind and trader._scanner_failover(attempted):
                continue
            raise


def path_pairs_exist_with_rpc_failover(
    trader,
    factory_contract,
    path: list[str],
    pair_cache: dict[tuple[str, str], str | None],
) -> tuple[bool, str]:
    """Validate pairs without converting provider errors into missing-pair facts."""
    for a, b in zip(path, path[1:]):
        aa = _route.Web3.to_checksum_address(a)
        bb = _route.Web3.to_checksum_address(b)
        key = tuple(sorted((aa.lower(), bb.lower())))

        if key in pair_cache:
            if not pair_cache[key]:
                return False, f"missing_v2_pair:{aa}>{bb}"
            continue

        attempted: set[int] = set()
        while True:
            try:
                # Rebind the factory contract to the trader's *current* Web3;
                # cycle_quote may have switched RPCs since the original factory
                # object was created.
                factory = trader.w3.eth.contract(
                    address=factory_contract.address,
                    abi=_route.FACTORY_ABI,
                )
                pair = _route.Web3.to_checksum_address(
                    factory.functions.getPair(aa, bb).call()
                )
                if pair.lower() == _route.ZERO_ADDRESS.lower():
                    pair_cache[key] = None
                    return False, f"missing_v2_pair:{aa}>{bb}"

                # Only a successful no-code response is structural absence.  An
                # exception remains provider uncertainty and is never cached as
                # a missing pool.
                if not trader.w3.eth.get_code(pair):
                    pair_cache[key] = None
                    return False, f"missing_v2_pair:{aa}>{bb}"

                pair_cache[key] = pair
                break
            except Exception as exc:
                kind = _rpc_error_kind(exc)
                if kind and _can_failover(trader) and trader._scanner_failover(attempted):
                    continue
                reason = kind or type(exc).__name__
                return False, f"pair_lookup_provider_error:{reason}"

    return True, "pairs_ok"


def scan_live_routes_with_rpc_failover(app, contexts):
    """Retry only an unhandled transient provider failure for the whole read-only scan."""
    last_kind = "provider_transport"
    for _attempt in range(_MAX_SCAN_ATTEMPTS):
        try:
            return _BASE_SCAN_LIVE_ROUTES(app, contexts)
        except Exception as exc:
            kind = _rpc_error_kind(exc)
            if not kind:
                raise
            last_kind = kind
            # A fresh scan constructs a new scanner-only trader.  Its bounded
            # cursor rotates the starting endpoint; no route or safety criterion
            # is changed.
            continue
    raise RuntimeError(
        f"Route scanner provider unavailable after bounded retry ({last_kind})"
    ) from None


def install() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(_route, "_route_scanner_rpc_failover_installed", False):
        return

    _route.LiveTrader = ScannerFailoverLiveTrader
    _route._resolve_factory = resolve_factory_with_rpc_failover
    _route._path_pairs_exist = path_pairs_exist_with_rpc_failover
    _route.scan_live_routes = scan_live_routes_with_rpc_failover
    _route._route_scanner_rpc_failover_installed = True
    _INSTALLED = True
    print(
        "[route-scanner-rpc-failover] installed=true scope=read-only max_attempts=3 "
        "provider-errors-not-cached-as-missing=true signer=off safety-gates=unchanged",
        flush=True,
    )


install()
