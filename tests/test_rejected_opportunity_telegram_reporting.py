from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

# Focused regression coverage for refused-opportunity Telegram reporting.


def test_format_rejected_telegram_includes_pool_usd_and_sol_equivalent():
    from learnerbot.rejected_opportunity_publisher import format_rejected_telegram

    text = format_rejected_telegram(
        candidate_id="candidate-1",
        chain="solana",
        token_address="Mint111",
        pool_address="Pool111",
        dex="raydium",
        source="gpt",
        source_strategy_id="gpt-leader-quality-v1",
        rejection_class="POOLCHECK_SHADOW_ONLY",
        rejection_reason="Large Amount of LP Unlocked",
        payload={"token_symbol": "TEST", "price_usd": "0.0123"},
        liquidity_usd=Decimal("62123.40"),
        sol_usd=Decimal("176.28"),
        pool_sol_equivalent=Decimal("352.4052643510324483775811210"),
    )

    assert "⛔ REFUSED OPPORTUNITY" in text
    assert "Pool value USD: $62,123.40" in text
    assert "Pool value SOL equivalent: 352.41 SOL" in text
    assert "SOL/USD: $176.28" in text
    assert "Token price: $0.01" in text
    assert "Reason: Large Amount of LP Unlocked" in text
    assert "Pool: Pool111" in text


def test_market_values_use_captured_liquidity_without_pool_lookup(monkeypatch):
    import learnerbot.rejected_opportunity_publisher as publisher

    def fail_lookup(**kwargs):
        raise AssertionError("DexScreener liquidity fallback should not run")

    monkeypatch.setattr(publisher, "_dexscreener_liquidity_usd", fail_lookup)
    liquidity, sol_usd, pool_sol = publisher._market_values(
        chain="solana",
        pool_address="Pool111",
        token_address="Mint111",
        payload={"liquidity_usd": "50000", "sol_usd": "200"},
    )
    assert liquidity == Decimal("50000")
    assert sol_usd == Decimal("200")
    assert pool_sol == Decimal("250")


def test_publish_rejection_notifies_only_new_observation(monkeypatch):
    import learnerbot.rejected_opportunity_publisher as publisher

    class FakeQueue:
        def __init__(self, inserted: bool):
            self.inserted = inserted
            self.calls = []

        def publish(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                candidate_id="candidate-queue",
                status="NEW",
                inserted_observation=self.inserted,
            )

    alerts = []
    queue = FakeQueue(inserted=True)
    monkeypatch.setattr(publisher, "_enabled", lambda: True)
    monkeypatch.setattr(publisher, "_queue", lambda: queue)
    monkeypatch.setattr(publisher, "notify_rejection_only", lambda **kwargs: alerts.append(kwargs))

    result = publisher.publish_rejection(
        chain="solana",
        token_address="Mint111",
        rejection_class="POOL_RISK_REJECT",
        rejection_reason="LP unlocked",
        source="gpt",
        source_strategy_id="strategy-1",
        source_event_id="event-1",
        payload={"pool_id": "Pool111", "liquidity_usd": "50000"},
        require_market_reason=False,
    )
    assert result.candidate_id == "candidate-queue"
    assert queue.calls[0]["pool_address"] == "Pool111"
    assert len(alerts) == 1
    assert alerts[0]["pool_address"] == "Pool111"

    queue.inserted = False
    publisher.publish_rejection(
        chain="solana",
        token_address="Mint111",
        rejection_class="POOL_RISK_REJECT",
        rejection_reason="LP unlocked",
        source="gpt",
        source_strategy_id="strategy-1",
        source_event_id="event-1",
        payload={"pool_id": "Pool111", "liquidity_usd": "50000"},
        require_market_reason=False,
    )
    assert len(alerts) == 1


def test_poolcheck_shadow_only_alerts_without_queue_publish(monkeypatch):
    import learnerbot.rejected_opportunity_publisher as publisher
    from sibot1_engines._shared import rejected_poolcheck_patch as poolcheck_patch

    alerts = []
    queued = []
    monkeypatch.setattr(
        poolcheck_patch,
        "_ORIGINAL",
        lambda self, intent: SimpleNamespace(
            verdict="SHADOW_ONLY",
            reasons=("RugCheck: Large Amount of LP Unlocked",),
        ),
    )
    monkeypatch.setattr(publisher, "notify_rejection_only", lambda **kwargs: alerts.append(kwargs))
    monkeypatch.setattr(poolcheck_patch, "publish_intent_rejection", lambda *args, **kwargs: queued.append((args, kwargs)))

    intent = SimpleNamespace(
        intent_id="gpt-sol-1",
        engine_id="gpt",
        strategy_id="gpt-leader-quality-v1",
        chain="solana",
        asset_out="Mint111",
        venue="raydium",
        market_event_id="event-1",
        metadata={"pool_id": "Pool111", "liquidity_usd": "50000", "price": "0.01"},
    )

    decision = poolcheck_patch._assess_entry(object(), intent)
    assert decision.verdict == "SHADOW_ONLY"
    assert queued == []
    assert len(alerts) == 1
    assert alerts[0]["rejection_class"] == "POOLCHECK_SHADOW_ONLY"
    assert alerts[0]["pool_address"] == "Pool111"
    assert alerts[0]["payload"]["liquidity_usd"] == "50000"
