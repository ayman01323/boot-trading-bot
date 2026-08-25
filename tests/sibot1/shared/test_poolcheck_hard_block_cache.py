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


def _gate(tmp_path):
    evidence = MarketEvidenceBook()
    evidence.put("e1", {"mint": "MINT"})
    return MandatoryShadowPoolCheck(tmp_path, evidence)


def test_structural_hard_block_cache_requires_unchanged_evidence(monkeypatch, tmp_path):
    calls = []

    def fake_external(mint, cfg):
        calls.append(mint)
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "TOKEN_SECURITY_SEVERE",
            "reason": "RugCheck severe token/pool risk: Freeze Authority Enabled",
            "evidence": {
                "rugcheck_blocking_risk": "Freeze Authority Enabled",
                "rugcheck_risks": ["Freeze Authority Enabled"],
            },
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    gate = _gate(tmp_path)

    first = gate.assess_entry(_intent("MINT"))
    second = gate.assess_entry(_intent("MINT"))

    assert first.verdict == "HARD_BLOCK"
    assert second.verdict == "HARD_BLOCK"
    # The provider layer is consulted again so changed evidence can invalidate the
    # local block; the provider module itself owns its network-response cache.
    assert calls == ["MINT", "MINT"]
    assert second.evidence["poolcheck_hard_block_cache_hit"] is True
    assert any("duplicate structural HARD_BLOCK" in reason for reason in second.reasons)


def test_changed_structural_evidence_invalidates_local_hard_block(monkeypatch, tmp_path):
    calls = []

    def fake_external(mint, cfg):
        calls.append(mint)
        risk = "Freeze Authority Enabled" if len(calls) == 1 else "Mint Authority Enabled"
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "TOKEN_SECURITY_SEVERE",
            "reason": f"RugCheck severe token/pool risk: {risk}",
            "evidence": {"rugcheck_blocking_risk": risk, "rugcheck_risks": [risk]},
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    gate = _gate(tmp_path)

    first = gate.assess_entry(_intent("MINT"))
    second = gate.assess_entry(_intent("MINT"))

    assert first.verdict == "HARD_BLOCK"
    assert second.verdict == "HARD_BLOCK"
    assert calls == ["MINT", "MINT"]
    assert "poolcheck_hard_block_cache_hit" not in second.evidence
    assert "Mint Authority Enabled" in second.reasons[0]


def test_provider_outage_is_shadow_only_and_not_cached(monkeypatch, tmp_path):
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
    gate = _gate(tmp_path)

    first = gate.assess_entry(_intent("MINT"))
    second = gate.assess_entry(_intent("MINT"))

    assert first.verdict == "SHADOW_ONLY"
    assert second.verdict == "SHADOW_ONLY"
    assert first.evidence["live_eligible"] is False
    assert second.evidence["live_eligible"] is False
    assert calls == ["MINT", "MINT"]
    assert "poolcheck_hard_block_cache_hit" not in second.evidence


def test_lp_unlocked_overpromotion_is_reclassified_to_shadow_only(monkeypatch, tmp_path):
    def fake_external(mint, cfg):
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "TOKEN_SECURITY_SEVERE",
            "reason": "RugCheck severe token/pool risk: Large Amount of LP Unlocked",
            "evidence": {
                "rugcheck_blocking_risk": "Large Amount of LP Unlocked",
                "rugcheck_risks": ["Large Amount of LP Unlocked"],
            },
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    decision = _gate(tmp_path).assess_entry(_intent("MINT"))

    assert decision.verdict == "SHADOW_ONLY"
    assert decision.evidence["live_eligible"] is False
    assert decision.evidence["poolcheck_reclassified_from"] == "HARD_BLOCK"
    assert decision.evidence["poolcheck_reclassification"] == "LIQUIDITY_ONLY_TO_SHADOW"


def test_structural_risk_wins_over_liquidity_risk(monkeypatch, tmp_path):
    def fake_external(mint, cfg):
        return {
            "decision": "HARD_BLOCK",
            "reason_code": "TOKEN_SECURITY_SEVERE",
            "reason": "RugCheck severe token/pool risk: Freeze Authority Enabled",
            "evidence": {
                "rugcheck_blocking_risk": "Freeze Authority Enabled",
                "rugcheck_risks": ["Large Amount of LP Unlocked", "Freeze Authority Enabled"],
            },
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    decision = _gate(tmp_path).assess_entry(_intent("MINT"))

    assert decision.verdict == "HARD_BLOCK"


def test_cooling_is_still_shadow_eligible_but_never_live_eligible(monkeypatch, tmp_path):
    def fake_external(mint, cfg):
        return {
            "decision": "COOLING",
            "reason_code": "POOL_NEW_COOLING",
            "reason": "new pool cooling period",
            "evidence": {"cooling_remaining_seconds": 600},
        }

    monkeypatch.setattr(solana_pool_risk_gate, "external_pool_check", fake_external)
    decision = _gate(tmp_path).assess_entry(_intent("MINT"))

    assert decision.verdict == "SHADOW_ONLY"
    assert decision.evidence["shadow_cooling"] is True
    assert decision.evidence["live_eligible"] is False
    assert decision.evidence["source_poolcheck_verdict"] == "COOLING"
