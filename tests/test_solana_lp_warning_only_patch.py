from learnerbot import solana_lp_warning_only_patch as patch


def _result(decision, code, *, blocking=""):
    evidence = {}
    if blocking:
        evidence["rugcheck_blocking_risk"] = blocking
    return {"decision": decision, "reason_code": code, "reason": "test", "evidence": evidence}


def test_lp_concentration_is_warning_only(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_EVALUATE_RUGCHECK",
        lambda summary, cfg: _result("SHADOW_ONLY", "LP_CONCENTRATION_RISK"),
    )
    summary = {
        "lpLockedPct": 0,
        "risks": [{"name": "Low amount of LP Providers", "level": "warn"}],
    }
    got = patch.evaluate_rugcheck_lp_warning_only(summary, {})
    assert got["decision"] == "PASS"
    assert got["reason_code"] == "LP_WARNING_ONLY"
    assert got["evidence"]["lp_warning_only"] is True
    assert got["evidence"]["lp_revalidation_required"] is False


def test_severe_lp_unlocked_is_warning_only(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_EVALUATE_RUGCHECK",
        lambda summary, cfg: _result(
            "HARD_BLOCK", "TOKEN_SECURITY_SEVERE", blocking="Large Amount of LP Unlocked"
        ),
    )
    summary = {
        "risks": [
            {
                "name": "Large Amount of LP Unlocked",
                "level": "danger",
                "value": "100.00%",
                "description": "LP tokens are unlocked",
            }
        ]
    }
    got = patch.evaluate_rugcheck_lp_warning_only(summary, {})
    assert got["decision"] == "PASS"
    assert got["reason_code"] == "LP_WARNING_ONLY"
    assert any("Large Amount of LP Unlocked" in x for x in got["evidence"]["lp_warning_messages"])


def test_score_only_lp_severe_can_be_warning(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_EVALUATE_RUGCHECK",
        lambda summary, cfg: _result("HARD_BLOCK", "TOKEN_SECURITY_SEVERE"),
    )
    summary = {
        "risks": [{"name": "Large Amount of LP Unlocked", "level": "danger", "value": "100%"}]
    }
    got = patch.evaluate_rugcheck_lp_warning_only(summary, {})
    assert got["decision"] == "PASS"
    assert got["reason_code"] == "LP_WARNING_ONLY"


def test_structural_danger_remains_hard_block(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_EVALUATE_RUGCHECK",
        lambda summary, cfg: _result("HARD_BLOCK", "TOKEN_SECURITY_SEVERE", blocking="Freeze Authority"),
    )
    summary = {
        "risks": [
            {"name": "Large Amount of LP Unlocked", "level": "danger", "value": "100%"},
            {"name": "Freeze Authority", "level": "danger"},
        ]
    }
    got = patch.evaluate_rugcheck_lp_warning_only(summary, {})
    assert got["decision"] == "HARD_BLOCK"
    assert got["reason_code"] == "TOKEN_SECURITY_SEVERE"


def test_unknown_non_lp_severe_remains_hard_block(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_EVALUATE_RUGCHECK",
        lambda summary, cfg: _result("HARD_BLOCK", "TOKEN_SECURITY_SEVERE"),
    )
    summary = {"risks": [{"name": "Unknown severe ownership control", "level": "danger"}]}
    got = patch.evaluate_rugcheck_lp_warning_only(summary, {})
    assert got["decision"] == "HARD_BLOCK"


def test_warning_identity_does_not_use_signal_signature():
    warnings = ["Large Amount of LP Unlocked (100%)"]
    a = patch._warning_key("123", "MintABC", warnings)
    b = patch._warning_key("123", "MintABC", list(warnings))
    assert a == b
    assert a.startswith("telegram_lp_warning_once:v1:")
