import pytest

from grok_known_assets_bot.core import Asset, Journal, RiskConfig, StrategyEngine
from grok_known_assets_bot.feed_safety import (
    FeedSafetyError,
    JupiterRouteEvidence,
    PoolSafetyEvidence,
    ProviderObservation,
    SafeSnapshotBuilder,
)

NOW_MS = 1_000_000


def assets():
    return {
        "solana:SOL:NATIVE": Asset("solana:SOL:NATIVE", "solana", "SOL", "NATIVE", True),
        "solana:MEME:VerifiedMint111": Asset(
            "solana:MEME:VerifiedMint111", "solana", "MEME", "VerifiedMint111", True
        ),
    }


def observation(
    *,
    address="NATIVE",
    provider="dexscreener",
    age_ms=1_000,
    received_age_ms=500,
    price_usd=100.0,
    **overrides,
):
    row = dict(
        provider=provider,
        chain="solana",
        address=address,
        source_timestamp_ms=NOW_MS - age_ms,
        received_at_ms=NOW_MS - received_age_ms,
        price_usd=price_usd,
        liquidity_usd=1_000_000.0,
        volume_5m_usd=100_000.0,
        ret_1m_pct=0.4,
        ret_5m_pct=1.2,
        ret_15m_pct=2.0,
        vol_5m_pct=1.5,
        pool_id="pool-1",
        block_or_slot=12345,
    )
    row.update(overrides)
    return ProviderObservation(**row)


def route(*, address="NATIVE", age_ms=500, **overrides):
    row = dict(
        chain="solana",
        address=address,
        checked_at_ms=NOW_MS - age_ms,
        forward_ok=True,
        reverse_ok=True,
        bid=99.9,
        ask=100.0,
        reverse_bid=99.8,
        impact_bps=10.0,
        fees_bps=5.0,
        slippage_bps=7.0,
        route_id="jup-route-1",
    )
    row.update(overrides)
    return JupiterRouteEvidence(**row)


def rug(*, address="VerifiedMint111", age_ms=120_000, passed=True, **overrides):
    row = dict(
        chain="solana",
        address=address,
        checked_at_ms=NOW_MS - age_ms,
        passed=passed,
        score=12.0,
        is_mint_renounced=True,
        is_freezable=False,
        top10_holders_pct=30.0,
        liquidity_locked_pct=90.0,
    )
    row.update(overrides)
    return PoolSafetyEvidence(**row)


def builder():
    return SafeSnapshotBuilder(assets(), RiskConfig())


def test_valid_native_feed_builds_canonical_snapshot():
    env = builder().build(
        chain="solana",
        address="NATIVE",
        observations=(observation(),),
        jupiter=route(),
        pool_safety=None,
        now_ms=NOW_MS,
    )
    assert env.canonical_asset_key == "solana:SOL:NATIVE"
    assert env.snapshot.asset_key == env.canonical_asset_key
    assert env.snapshot.sellable is True
    assert env.field_sources["bid"] == "jupiter"


def test_non_native_requires_pool_safety():
    with pytest.raises(FeedSafetyError, match="MISSING_POOL_SAFETY"):
        builder().build(
            chain="solana",
            address="VerifiedMint111",
            observations=(observation(address="VerifiedMint111"),),
            jupiter=route(address="VerifiedMint111"),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_failed_pool_safety_rejects_before_strategy():
    with pytest.raises(FeedSafetyError, match="POOL_SAFETY_REJECT"):
        builder().build(
            chain="solana",
            address="VerifiedMint111",
            observations=(observation(address="VerifiedMint111"),),
            jupiter=route(address="VerifiedMint111"),
            pool_safety=rug(passed=False),
            now_ms=NOW_MS,
        )


def test_unlisted_canonical_identity_is_rejected():
    with pytest.raises(FeedSafetyError, match="UNLISTED_CANONICAL_IDENTITY"):
        builder().build(
            chain="solana",
            address="UnknownMint",
            observations=(observation(address="UnknownMint"),),
            jupiter=route(address="UnknownMint"),
            pool_safety=rug(address="UnknownMint"),
            now_ms=NOW_MS,
        )


def test_provider_identity_mismatch_is_rejected():
    with pytest.raises(FeedSafetyError, match="PROVIDER_IDENTITY_MISMATCH"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=(observation(address="WrongMint"),),
            jupiter=route(),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_stale_provider_is_rejected_by_provider_ttl():
    with pytest.raises(FeedSafetyError, match="STALE_PROVIDER:dexscreener"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=(observation(age_ms=25_000),),
            jupiter=route(),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_future_receipt_timestamp_is_rejected():
    with pytest.raises(FeedSafetyError, match="RECEIPT_IN_FUTURE"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=(observation(received_at_ms=NOW_MS + 5_000),),
            jupiter=route(),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_stale_jupiter_route_is_rejected():
    with pytest.raises(FeedSafetyError, match="STALE_PROVIDER:jupiter"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=(observation(),),
            jupiter=route(age_ms=6_000),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_reverse_jupiter_route_is_mandatory():
    with pytest.raises(FeedSafetyError, match="NO_JUPITER_REVERSE_ROUTE"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=(observation(),),
            jupiter=route(reverse_ok=False, reverse_bid=0.0),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_provider_price_disagreement_halts_snapshot():
    observations = (
        observation(provider="dexscreener", price_usd=100.0),
        observation(provider="birdeye", price_usd=102.5, age_ms=800),
    )
    with pytest.raises(FeedSafetyError, match="PROVIDER_PRICE_DISAGREEMENT"):
        builder().build(
            chain="solana",
            address="NATIVE",
            observations=observations,
            jupiter=route(),
            pool_safety=None,
            now_ms=NOW_MS,
        )


def test_rugcheck_ttl_does_not_make_fresh_market_quote_stale():
    env = builder().build(
        chain="solana",
        address="VerifiedMint111",
        observations=(observation(address="VerifiedMint111", age_ms=1_000),),
        jupiter=route(address="VerifiedMint111", age_ms=500),
        pool_safety=rug(age_ms=120_000),
        now_ms=NOW_MS,
    )
    assert env.market_data_max_age_ms == 1_000
    assert env.safety_evidence_max_age_ms == 120_000
    assert env.snapshot.ts == pytest.approx((NOW_MS - 1_000) / 1000.0)


def test_freshest_provider_is_recorded_per_field():
    old = observation(provider="dexscreener", age_ms=5_000, liquidity_usd=900_000.0)
    fresh = observation(provider="birdeye", age_ms=500, price_usd=100.2, liquidity_usd=1_100_000.0)
    env = builder().build(
        chain="solana",
        address="NATIVE",
        observations=(old, fresh),
        jupiter=route(),
        pool_safety=None,
        now_ms=NOW_MS,
    )
    assert env.snapshot.liquidity_usd == 1_100_000.0
    assert env.field_sources["liquidity_usd"] == "birdeye"


def test_round_trip_cost_model_includes_explicit_slippage(tmp_path):
    risk = RiskConfig()
    engine = StrategyEngine(assets(), risk, Journal(tmp_path / "state.sqlite3"))
    env = builder().build(
        chain="solana",
        address="NATIVE",
        observations=(observation(),),
        jupiter=route(),
        pool_safety=None,
        now_ms=NOW_MS,
    )
    # 10 spread + 2*5 fees + 2*10 impact + 2*7 slippage = 54 bps = 0.54%.
    assert engine._estimated_round_trip_cost_pct(env.snapshot) == pytest.approx(0.54)


def test_day_start_equity_persists_across_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    first = Journal(db)
    assert first.day_start_equity(1_800_000_000.0, 10_000.0) == 10_000.0
    second = Journal(db)
    assert second.day_start_equity(1_800_000_100.0, 7_500.0) == 10_000.0


def test_partial_close_does_not_count_as_consecutive_loss(tmp_path):
    journal = Journal(tmp_path / "state.sqlite3")
    journal.accumulate_trade_pnl("trade-1", -10.0, final=False, asset_key="solana:SOL:NATIVE")
    assert journal.consecutive_losses() == 0
    journal.accumulate_trade_pnl("trade-1", -5.0, final=True, asset_key="solana:SOL:NATIVE")
    assert journal.consecutive_losses() == 1


def test_completed_winner_resets_consecutive_loss_streak(tmp_path):
    journal = Journal(tmp_path / "state.sqlite3")
    journal.accumulate_trade_pnl("loss-1", -10.0, final=True, asset_key="solana:SOL:NATIVE")
    journal.accumulate_trade_pnl("loss-2", -5.0, final=True, asset_key="solana:SOL:NATIVE")
    assert journal.consecutive_losses() == 2
    journal.accumulate_trade_pnl("win-1", 1.0, final=True, asset_key="solana:SOL:NATIVE")
    assert journal.consecutive_losses() == 0
