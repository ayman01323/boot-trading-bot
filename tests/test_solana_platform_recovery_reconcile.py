from learnerbot import solana_platform_recovery_reconcile_patch as patch


BLOCK = "platform amount gate is in recovery mode and another LIVE position is still open"


def test_stale_open_row_is_rechecked_after_verified_reconciliation(monkeypatch):
    calls = {"n": 0}

    def previous(app, cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, BLOCK, {"profit_factor": "0"}, False
        return True, "RECOVERY_CANARY", {"profit_factor": "0"}, True

    monkeypatch.setattr(patch, "_PREV_PLATFORM_AMOUNT_GATE", previous)
    monkeypatch.setattr(patch, "_reconcile_open_live_positions", lambda app: (0, True))

    result = patch.platform_amount_gate(object(), {})
    assert result[0] is True
    assert result[1] == "RECOVERY_CANARY"
    assert result[3] is True
    assert calls["n"] == 2


def test_genuine_verified_open_live_position_still_blocks(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_PLATFORM_AMOUNT_GATE",
        lambda app, cfg: (False, BLOCK, {"profit_factor": "0"}, False),
    )
    monkeypatch.setattr(patch, "_reconcile_open_live_positions", lambda app: (1, True))

    ok, reason, metrics, recovery = patch.platform_amount_gate(object(), {})
    assert ok is False
    assert reason == BLOCK
    assert recovery is False


def test_reconciliation_uncertainty_fails_closed(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_PLATFORM_AMOUNT_GATE",
        lambda app, cfg: (False, BLOCK, {"profit_factor": "0"}, False),
    )
    monkeypatch.setattr(patch, "_reconcile_open_live_positions", lambda app: (0, False))

    ok, reason, metrics, recovery = patch.platform_amount_gate(object(), {})
    assert ok is False
    assert "cannot prove recovery canary exclusivity" in reason
    assert recovery is False


def test_non_position_blockers_are_not_bypassed(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_PLATFORM_AMOUNT_GATE",
        lambda app, cfg: (False, "platform PF 0.500 is below 1.30", {"profit_factor": "0.5"}, False),
    )
    monkeypatch.setattr(
        patch,
        "_reconcile_open_live_positions",
        lambda app: (_ for _ in ()).throw(AssertionError("must not reconcile unrelated blockers")),
    )

    ok, reason, metrics, recovery = patch.platform_amount_gate(object(), {})
    assert ok is False
    assert "PF 0.500" in reason
    assert recovery is False
