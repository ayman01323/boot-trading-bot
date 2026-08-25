from decimal import Decimal

import pytest

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent


def test_market_event_is_constructible_without_execution_side_effects():
    event = MarketEvent(
        event_id="evt-1",
        chain="base",
        observed_at_ms=1,
        source="test",
        event_type="pool_update",
        asset_in="USDC",
        asset_out="WETH",
    )
    assert event.chain == "base"


def test_trade_intent_requires_positive_amount_and_supported_side():
    intent = TradeIntent(
        intent_id="i-1",
        engine_id="gpt",
        engine_version="1.0",
        strategy_id="test",
        chain="base",
        side="BUY",
        asset_in="USDC",
        asset_out="WETH",
        requested_input_amount=Decimal("1"),
        created_at_ms=1,
    )
    assert intent.requested_input_amount == Decimal("1")

    with pytest.raises(ValueError):
        TradeIntent(
            intent_id="i-2",
            engine_id="gpt",
            engine_version="1.0",
            strategy_id="test",
            chain="base",
            side="SELL",
            asset_in="WETH",
            asset_out="USDC",
            requested_input_amount=Decimal("1"),
            created_at_ms=1,
        )


def test_exit_intent_requires_owned_lot_or_asset_selector():
    with pytest.raises(ValueError):
        ExitIntent(
            intent_id="x-1",
            engine_id="gpt",
            engine_version="1.0",
            strategy_id="test",
            chain="base",
            created_at_ms=1,
        )

    exit_intent = ExitIntent(
        intent_id="x-2",
        engine_id="gpt",
        engine_version="1.0",
        strategy_id="test",
        chain="base",
        created_at_ms=1,
        lot_id="lot-gpt-1",
        exit_fraction=Decimal("1"),
    )
    assert exit_intent.lot_id == "lot-gpt-1"
