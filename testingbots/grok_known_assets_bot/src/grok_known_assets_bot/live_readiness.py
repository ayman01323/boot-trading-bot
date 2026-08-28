from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from .core import MarketSnapshot
from .feed_safety import FeedSafetyError
from .live_feed import USDC_MINT, SOL_MINT, LiveFeedSettings, _price_impact_bps, _quote, _route_pool_ids

CANARY_SOL = 0.0005
HARD_MAX_CANARY_SOL = 0.001
MAX_SIGNAL_AGE_SECONDS = 20.0
MAX_ENTRY_IMPACT_BPS = 100.0
MAX_REVERSE_IMPACT_BPS = 200.0
MAX_STRESS_IMPACT_BPS = 500.0
MAX_ROUNDTRIP_LOSS_PCT = 3.0
STRESS_MULTIPLIER = 3


@dataclass(frozen=True)
class LiveReadinessResult:
    ready: bool
    reason: str
    asset_key: str
    canary_target_sol: float
    estimated_spend_usdc: float
    quoted_sol_out: float
    reverse_recovery_usdc: float
    roundtrip_loss_pct: float
    entry_impact_bps: float
    reverse_impact_bps: float
    stress_impact_bps: float
    slippage_bps: int
    route_id: str
    expires_epoch: int
    signing_enabled: bool = False
    broadcast_enabled: bool = False

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _result(
    snap: MarketSnapshot,
    settings: LiveFeedSettings,
    *,
    ready: bool,
    reason: str,
    spend_usdc: float = 0.0,
    sol_out: float = 0.0,
    reverse_usdc: float = 0.0,
    roundtrip_loss_pct: float = 0.0,
    entry_impact_bps: float = 0.0,
    reverse_impact_bps: float = 0.0,
    stress_impact_bps: float = 0.0,
    route_id: str = "",
    now: float,
) -> LiveReadinessResult:
    return LiveReadinessResult(
        ready=ready,
        reason=reason,
        asset_key=snap.asset_key,
        canary_target_sol=CANARY_SOL,
        estimated_spend_usdc=spend_usdc,
        quoted_sol_out=sol_out,
        reverse_recovery_usdc=reverse_usdc,
        roundtrip_loss_pct=roundtrip_loss_pct,
        entry_impact_bps=entry_impact_bps,
        reverse_impact_bps=reverse_impact_bps,
        stress_impact_bps=stress_impact_bps,
        slippage_bps=int(settings.slippage_bps),
        route_id=route_id,
        expires_epoch=int(now + MAX_SIGNAL_AGE_SECONDS),
        signing_enabled=False,
        broadcast_enabled=False,
    )


def assess_live_readiness(
    snap: MarketSnapshot,
    settings: LiveFeedSettings,
    *,
    now: float | None = None,
) -> LiveReadinessResult:
    """Prove an unsigned USDC->SOL canary route plus reversible exits.

    This is intentionally a pre-signing boundary. It performs fresh public Jupiter
    route checks only and never accesses a wallet, private key, signer or broadcast
    endpoint.
    """
    now = float(time.time() if now is None else now)
    age = max(0.0, now - float(snap.ts))
    if age > MAX_SIGNAL_AGE_SECONDS:
        return _result(snap, settings, ready=False, reason="SIGNAL_TOO_OLD", now=now)
    if not snap.sellable:
        return _result(snap, settings, ready=False, reason="REVERSE_SELL_PATH_UNAVAILABLE", now=now)
    if CANARY_SOL > HARD_MAX_CANARY_SOL:
        return _result(snap, settings, ready=False, reason="CANARY_EXCEEDS_HARD_MAX", now=now)

    estimated_spend = max(0.000001, float(snap.ask) * CANARY_SOL)
    micro_usdc = max(1, int(round(estimated_spend * 1_000_000)))

    try:
        entry = _quote(
            USDC_MINT,
            SOL_MINT,
            micro_usdc,
            slippage_bps=settings.slippage_bps,
            timeout=settings.request_timeout_seconds,
        )
        sol_raw = int(entry.get("outAmount") or 0)
        if sol_raw <= 0:
            raise FeedSafetyError("ENTRY_QUOTE_NO_OUTPUT")

        reverse = _quote(
            SOL_MINT,
            USDC_MINT,
            sol_raw,
            slippage_bps=settings.slippage_bps,
            timeout=settings.request_timeout_seconds,
        )
        reverse_raw = int(reverse.get("outAmount") or 0)
        if reverse_raw <= 0:
            raise FeedSafetyError("REVERSE_QUOTE_NO_OUTPUT")

        stress = _quote(
            SOL_MINT,
            USDC_MINT,
            max(1, sol_raw * STRESS_MULTIPLIER),
            slippage_bps=settings.slippage_bps,
            timeout=settings.request_timeout_seconds,
        )
        stress_raw = int(stress.get("outAmount") or 0)
        if stress_raw <= 0:
            raise FeedSafetyError("STRESS_QUOTE_NO_OUTPUT")
    except (FeedSafetyError, OSError, ValueError, KeyError) as exc:
        return _result(
            snap,
            settings,
            ready=False,
            reason=f"JUPITER_PREFLIGHT_FAILED:{type(exc).__name__}:{exc}",
            spend_usdc=micro_usdc / 1_000_000.0,
            now=now,
        )

    spend_usdc = micro_usdc / 1_000_000.0
    sol_out = sol_raw / 1_000_000_000.0
    reverse_usdc = reverse_raw / 1_000_000.0
    entry_impact = _price_impact_bps(entry)
    reverse_impact = _price_impact_bps(reverse)
    stress_impact = _price_impact_bps(stress)
    loss_pct = max(0.0, (1.0 - reverse_usdc / spend_usdc) * 100.0) if spend_usdc > 0 else 100.0
    route_id = "|".join(_route_pool_ids(entry, reverse, stress))

    common = dict(
        spend_usdc=spend_usdc,
        sol_out=sol_out,
        reverse_usdc=reverse_usdc,
        roundtrip_loss_pct=loss_pct,
        entry_impact_bps=entry_impact,
        reverse_impact_bps=reverse_impact,
        stress_impact_bps=stress_impact,
        route_id=route_id,
        now=now,
    )
    if entry_impact > MAX_ENTRY_IMPACT_BPS:
        return _result(snap, settings, ready=False, reason="ENTRY_IMPACT_TOO_HIGH", **common)
    if reverse_impact > MAX_REVERSE_IMPACT_BPS:
        return _result(snap, settings, ready=False, reason="REVERSE_IMPACT_TOO_HIGH", **common)
    if stress_impact > MAX_STRESS_IMPACT_BPS:
        return _result(snap, settings, ready=False, reason="STRESS_IMPACT_TOO_HIGH", **common)
    if loss_pct > MAX_ROUNDTRIP_LOSS_PCT:
        return _result(snap, settings, ready=False, reason="ROUNDTRIP_LOSS_TOO_HIGH", **common)

    return _result(snap, settings, ready=True, reason="LIVE_ROUTE_PREFLIGHT_PASS", **common)
