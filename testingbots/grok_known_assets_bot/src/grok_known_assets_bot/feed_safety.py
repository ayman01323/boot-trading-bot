from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .core import Asset, MarketSnapshot, RiskConfig


class FeedSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderObservation:
    provider: str
    chain: str
    address: str
    source_timestamp_ms: int
    received_at_ms: int
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_5m_usd: float | None = None
    ret_1m_pct: float | None = None
    ret_5m_pct: float | None = None
    ret_15m_pct: float | None = None
    vol_5m_pct: float | None = None
    pool_id: str | None = None
    block_or_slot: int | None = None


@dataclass(frozen=True)
class JupiterRouteEvidence:
    chain: str
    address: str
    checked_at_ms: int
    forward_ok: bool
    reverse_ok: bool
    bid: float
    ask: float
    reverse_bid: float
    impact_bps: float
    fees_bps: float
    slippage_bps: float
    route_id: str = ""


@dataclass(frozen=True)
class PoolSafetyEvidence:
    chain: str
    address: str
    checked_at_ms: int
    passed: bool
    provider: str = "rugcheck"
    score: float | None = None
    is_mint_renounced: bool | None = None
    is_freezable: bool | None = None
    top10_holders_pct: float | None = None
    liquidity_locked_pct: float | None = None


@dataclass(frozen=True)
class FeedSafetyPolicy:
    max_price_disagreement_pct: float = 1.00
    max_clock_skew_ms: int = 2_000
    require_rugcheck_for_non_native: bool = True
    provider_max_age_ms: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType(
            {
                "jupiter": 5_000,
                "birdeye": 10_000,
                "dexscreener": 20_000,
                "helius": 10_000,
                "alchemy": 10_000,
                "coingecko": 120_000,
                "rugcheck": 300_000,
            }
        )
    )


@dataclass(frozen=True)
class ValidatedSnapshotEnvelope:
    canonical_asset_key: str
    canonical_chain: str
    canonical_address: str
    snapshot: MarketSnapshot
    data_max_age_ms: int
    provider_disagreement_pct: float
    field_sources: Mapping[str, str]
    provenance: tuple[ProviderObservation, ...]
    jupiter: JupiterRouteEvidence
    pool_safety: PoolSafetyEvidence | None


def _identity(chain: str, address: str) -> tuple[str, str]:
    return chain.strip().lower(), address.strip()


class SafeSnapshotBuilder:
    """Fail-closed normalizer for real-feed PAPER inputs.

    Real provider data should enter the strategy only through this builder. It
    proves canonical identity, per-source freshness, provider consensus,
    executable Jupiter forward/reverse routes and non-native pool safety.
    """

    def __init__(
        self,
        assets: Mapping[str, Asset],
        risk: RiskConfig,
        policy: FeedSafetyPolicy | None = None,
    ):
        self.assets = dict(assets)
        self.risk = risk
        self.policy = policy or FeedSafetyPolicy()
        self._identity_to_asset: dict[tuple[str, str], Asset] = {}
        for asset in self.assets.values():
            if not asset.enabled:
                continue
            ident = _identity(asset.chain, asset.address)
            if ident in self._identity_to_asset:
                raise FeedSafetyError(f"duplicate enabled canonical identity: {ident}")
            self._identity_to_asset[ident] = asset

    def _asset_for_identity(self, chain: str, address: str) -> Asset:
        asset = self._identity_to_asset.get(_identity(chain, address))
        if asset is None:
            raise FeedSafetyError("UNLISTED_CANONICAL_IDENTITY")
        return asset

    def _max_age_ms(self, provider: str) -> int:
        return int(
            self.policy.provider_max_age_ms.get(
                provider.lower(), int(self.risk.max_quote_age_s * 1000.0)
            )
        )

    def _validate_timestamp(
        self,
        *,
        provider: str,
        source_timestamp_ms: int,
        received_at_ms: int,
        now_ms: int,
    ) -> int:
        if source_timestamp_ms <= 0 or received_at_ms <= 0:
            raise FeedSafetyError(f"INVALID_TIMESTAMP:{provider}")
        if source_timestamp_ms > received_at_ms + self.policy.max_clock_skew_ms:
            raise FeedSafetyError(f"SOURCE_AFTER_RECEIPT:{provider}")
        if received_at_ms > now_ms + self.policy.max_clock_skew_ms:
            raise FeedSafetyError(f"RECEIPT_IN_FUTURE:{provider}")
        age_ms = max(0, now_ms - source_timestamp_ms)
        if age_ms > self._max_age_ms(provider):
            raise FeedSafetyError(f"STALE_PROVIDER:{provider}:{age_ms}")
        return age_ms

    def _validate_jupiter(
        self,
        asset: Asset,
        route: JupiterRouteEvidence,
        now_ms: int,
    ) -> int:
        if _identity(route.chain, route.address) != _identity(asset.chain, asset.address):
            raise FeedSafetyError("JUPITER_IDENTITY_MISMATCH")
        age_ms = max(0, now_ms - int(route.checked_at_ms))
        if age_ms > self._max_age_ms("jupiter"):
            raise FeedSafetyError(f"STALE_PROVIDER:jupiter:{age_ms}")
        if not route.forward_ok:
            raise FeedSafetyError("NO_JUPITER_FORWARD_ROUTE")
        if not route.reverse_ok or route.reverse_bid <= 0.0:
            raise FeedSafetyError("NO_JUPITER_REVERSE_ROUTE")
        if route.bid <= 0.0 or route.ask <= 0.0 or route.ask < route.bid:
            raise FeedSafetyError("INVALID_JUPITER_MARKET")
        if route.impact_bps < 0.0 or route.impact_bps > self.risk.max_price_impact_bps:
            raise FeedSafetyError("JUPITER_IMPACT_OUT_OF_RANGE")
        if route.fees_bps < 0.0 or route.slippage_bps < 0.0:
            raise FeedSafetyError("NEGATIVE_JUPITER_COST")
        return age_ms

    def _validate_pool_safety(
        self,
        asset: Asset,
        evidence: PoolSafetyEvidence | None,
        now_ms: int,
    ) -> int:
        if asset.is_native and evidence is None:
            return 0
        if evidence is None:
            if self.policy.require_rugcheck_for_non_native:
                raise FeedSafetyError("MISSING_POOL_SAFETY")
            return 0
        if _identity(evidence.chain, evidence.address) != _identity(asset.chain, asset.address):
            raise FeedSafetyError("POOL_SAFETY_IDENTITY_MISMATCH")
        age_ms = max(0, now_ms - int(evidence.checked_at_ms))
        if age_ms > self._max_age_ms(evidence.provider):
            raise FeedSafetyError(f"STALE_PROVIDER:{evidence.provider}:{age_ms}")
        if not evidence.passed:
            raise FeedSafetyError("POOL_SAFETY_REJECT")
        return age_ms

    @staticmethod
    def _freshest_value(
        observations: tuple[ProviderObservation, ...],
        field_name: str,
    ) -> tuple[float, str]:
        eligible = [o for o in observations if getattr(o, field_name) is not None]
        if not eligible:
            raise FeedSafetyError(f"MISSING_FIELD:{field_name}")
        chosen = max(eligible, key=lambda o: o.source_timestamp_ms)
        return float(getattr(chosen, field_name)), chosen.provider.lower()

    def build(
        self,
        *,
        chain: str,
        address: str,
        observations: tuple[ProviderObservation, ...],
        jupiter: JupiterRouteEvidence,
        pool_safety: PoolSafetyEvidence | None,
        now_ms: int,
    ) -> ValidatedSnapshotEnvelope:
        if not observations:
            raise FeedSafetyError("NO_PROVIDER_OBSERVATIONS")
        asset = self._asset_for_identity(chain, address)
        canonical_identity = _identity(asset.chain, asset.address)

        ages: list[int] = []
        for obs in observations:
            if _identity(obs.chain, obs.address) != canonical_identity:
                raise FeedSafetyError(f"PROVIDER_IDENTITY_MISMATCH:{obs.provider}")
            ages.append(
                self._validate_timestamp(
                    provider=obs.provider,
                    source_timestamp_ms=int(obs.source_timestamp_ms),
                    received_at_ms=int(obs.received_at_ms),
                    now_ms=int(now_ms),
                )
            )

        ages.append(self._validate_jupiter(asset, jupiter, int(now_ms)))
        pool_age = self._validate_pool_safety(asset, pool_safety, int(now_ms))
        if pool_safety is not None:
            ages.append(pool_age)

        prices = [float(o.price_usd) for o in observations if o.price_usd is not None and o.price_usd > 0.0]
        disagreement_pct = 0.0
        if len(prices) >= 2:
            low, high = min(prices), max(prices)
            midpoint = (low + high) / 2.0
            disagreement_pct = ((high - low) / midpoint) * 100.0 if midpoint > 0.0 else 999.0
            if disagreement_pct > self.policy.max_price_disagreement_pct:
                raise FeedSafetyError(f"PROVIDER_PRICE_DISAGREEMENT:{disagreement_pct:.4f}")

        liquidity_usd, liquidity_source = self._freshest_value(observations, "liquidity_usd")
        volume_5m_usd, volume_source = self._freshest_value(observations, "volume_5m_usd")
        ret_1m_pct, ret_1m_source = self._freshest_value(observations, "ret_1m_pct")
        ret_5m_pct, ret_5m_source = self._freshest_value(observations, "ret_5m_pct")
        ret_15m_pct, ret_15m_source = self._freshest_value(observations, "ret_15m_pct")
        vol_5m_pct, vol_source = self._freshest_value(observations, "vol_5m_pct")

        spread_bps = ((jupiter.ask - jupiter.bid) / jupiter.ask) * 10_000.0
        if spread_bps < 0.0 or spread_bps > self.risk.max_spread_bps:
            raise FeedSafetyError(f"JUPITER_SPREAD_OUT_OF_RANGE:{spread_bps:.4f}")

        data_max_age_ms = max(ages) if ages else 0
        conservative_ts = (int(now_ms) - data_max_age_ms) / 1000.0
        snapshot = MarketSnapshot(
            asset_key=asset.key,
            ts=conservative_ts,
            bid=float(jupiter.bid),
            ask=float(jupiter.ask),
            reverse_bid=float(jupiter.reverse_bid),
            liquidity_usd=liquidity_usd,
            volume_5m_usd=volume_5m_usd,
            ret_1m_pct=ret_1m_pct,
            ret_5m_pct=ret_5m_pct,
            ret_15m_pct=ret_15m_pct,
            vol_5m_pct=vol_5m_pct,
            spread_bps=spread_bps,
            price_impact_bps=float(jupiter.impact_bps),
            fee_bps=float(jupiter.fees_bps),
            sellable=bool(jupiter.forward_ok and jupiter.reverse_ok),
            slippage_bps=float(jupiter.slippage_bps),
        )
        field_sources = MappingProxyType(
            {
                "bid": "jupiter",
                "ask": "jupiter",
                "reverse_bid": "jupiter",
                "spread_bps": "jupiter",
                "price_impact_bps": "jupiter",
                "fee_bps": "jupiter",
                "slippage_bps": "jupiter",
                "liquidity_usd": liquidity_source,
                "volume_5m_usd": volume_source,
                "ret_1m_pct": ret_1m_source,
                "ret_5m_pct": ret_5m_source,
                "ret_15m_pct": ret_15m_source,
                "vol_5m_pct": vol_source,
            }
        )
        return ValidatedSnapshotEnvelope(
            canonical_asset_key=asset.key,
            canonical_chain=asset.chain,
            canonical_address=asset.address,
            snapshot=snapshot,
            data_max_age_ms=data_max_age_ms,
            provider_disagreement_pct=round(disagreement_pct, 6),
            field_sources=field_sources,
            provenance=tuple(observations),
            jupiter=jupiter,
            pool_safety=pool_safety,
        )
