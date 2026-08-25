from decimal import Decimal

from learnerbot import solana_pool_risk_gate
from sibot1_engines._shared.contracts import TradeIntent
from sibot1_engines._shared.market_data import MarketEvidenceBook
from sibot1_engines._shared.poolcheck_bridge import MandatoryShadowPoolCheck


def _intent(mint: str) -> TradeIntent:
    return TradeIntent(
        intent_id="i1",
        engine_id="gemini",
        engine_version="1",
        strategy_id="test",
        chain="solana",
        side="BUY",
        asset_in="SOL",
        asset_out=mint,
        requested_input_amount=Decimal("1"),
        created_at_ms=1,
        market_event_id="e1",
    )


def test_structural_hard_block_is_cached_and_duplicate_provider_call_is_suppressed(monkeypatch, tmp_path):
    calls = []

    def fake_external(mint, cfg):
        calls.append(mint)
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "TOKEN_SECURITY_SEVERE",
            "reason": "RugCheck severe token/pool risk: Large Amount of LP Unlocked",
            "evidence": {"rugcheck_blocking_risk": "Large Amount of LP Unlocked"},
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    evidence = MarketEvidenceBook()
    evidence.put("e1", {"mint": "MINT"})
    gate = MandatoryShadowPoolCheck(tmp_path, evidence)

    first = gate.assess_entry(_intent("MINT"))
    second = gate.assess_entry(_intent("MINT"))

    assert first.verdict == "HARD_BLOCK"
    assert second.verdict == "HARD_BLOCK"
    assert calls == ["MINT"]
    assert second.evidence["poolcheck_hard_block_cache_hit"] is True
    assert any("duplicate HARD_BLOCK suppressed" in reason for reason in second.reasons)


def test_provider_outage_hard_block_is_not_cached(monkeypatch, tmp_path):
    calls = []

    def fake_external(mint, cfg):
        calls.append(mint)
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "RUGCHECK_UNAVAILABLE",
            "reason": "provider unavailable",
            "evidence": {"rugcheck_available": False},
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    evidence = MarketEvidenceBook()
    evidence.put("e1", {"mint": "MINT"})
    gate = MandatoryShadowPoolCheck(tmp_path, evidence)

    assert gate.assess_entry(_intent("MINT")).verdict == "HARD_BLOCK"
    assert gate.assess_entry(_intent("MINT")).verdict == "HARD_BLOCK"
    assert calls == ["MINT", "MINT"]
