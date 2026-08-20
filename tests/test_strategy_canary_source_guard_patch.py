from __future__ import annotations

from learnerbot.strategy_canary_source_guard_patch import guard_approval_state


def _state(source: str = "a" * 40):
    return {
        "source_commit": source,
        "approvals": {
            "liquidity confirmed momentum": {
                "strategy": "Liquidity Confirmed Momentum",
                "confidence": "0.95",
            }
        },
    }


def test_exact_deployed_source_preserves_canary_approval():
    source = "a" * 40
    guarded = guard_approval_state(_state(source), source)
    assert guarded["approval_source_match"] is True
    assert guarded["approvals"]
    assert "matches deployed source" in guarded["approval_guard_reason"]


def test_newly_deployed_source_cannot_inherit_old_cycle_approval():
    guarded = guard_approval_state(_state("a" * 40), "b" * 40)
    assert guarded["approval_source_match"] is False
    assert guarded["approvals"] == {}
    assert "does not match deployed source" in guarded["approval_guard_reason"]


def test_unverifiable_deployed_source_fails_closed():
    guarded = guard_approval_state(_state("a" * 40), "")
    assert guarded["approval_source_match"] is False
    assert guarded["approvals"] == {}
    assert "could not be verified" in guarded["approval_guard_reason"]


def test_empty_approval_set_remains_empty_without_false_authorisation():
    guarded = guard_approval_state({"source_commit": "a" * 40, "approvals": {}}, "b" * 40)
    assert guarded["approvals"] == {}
    assert guarded["approval_source_match"] is False
