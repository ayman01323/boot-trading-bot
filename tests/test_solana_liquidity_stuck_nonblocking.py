from __future__ import annotations

import time
from decimal import Decimal

from learnerbot import solana_liquidity_stuck_nonblocking_patch as patch


def _cfg():
    return {
        "live_liquidity_stuck_nonblocking": "true",
        "live_liquidity_stuck_min_attempts": "2",
        "live_liquidity_stuck_min_seconds": "60",
        "live_liquidity_stuck_max_quarantined": "3",
        "live_liquidity_stuck_owner_notice_hours": "12",
    }


def _stuck(now=None, **overrides):
    now = int(now or time.time())
    row = {
        "position_id": "pos-1",
        "mint": "Mint111111111111111111111111111111111111111",
        "recorded_raw": "87400222",
        "verified": True,
        "verified_balance_raw": "87405554",
        "wallets_checked": 1,
        "entry_ts": now - 600,
        "liquidity_state": "LIQUIDITY_STUCK",
        "liquidity_attempts": 3,
        "liquidity_first_blocked_epoch": now - 120,
        "safe_slice_percentages": ["100", "75", "50", "25", "10", "5", "2", "1"],
        "emergency_limit_bps": "500",
    }
    row.update(overrides)
    return row


def test_only_verified_durable_stuck_frees_capacity():
    now = 2_000_000_000
    cfg = _cfg()
    assert patch._is_verified_stuck(_stuck(now), cfg, now=now)
    assert not patch._is_verified_stuck(_stuck(now, verified=False), cfg, now=now)
    assert not patch._is_verified_stuck(_stuck(now, verified_balance_raw="0"), cfg, now=now)
    assert not patch._is_verified_stuck(_stuck(now, liquidity_attempts=1), cfg, now=now)
    assert not patch._is_verified_stuck(_stuck(now, liquidity_first_blocked_epoch=now - 20), cfg, now=now)
    assert not patch._is_verified_stuck(_stuck(now, liquidity_state="OPEN"), cfg, now=now)


def test_capacity_excludes_verified_stuck_but_uncertainty_fails_closed(monkeypatch):
    cfg = _cfg()
    row = _stuck()
    monkeypatch.setattr(patch, "_PREV_OPEN_COUNT", lambda app, tid: 1)
    monkeypatch.setattr(patch, "_cfg", lambda app: cfg)
    monkeypatch.setattr(patch, "_truth_for_tid", lambda app, tid, cfg: ([row], True))
    assert patch.open_live_count_without_verified_stuck(object(), "7") == 0

    monkeypatch.setattr(patch, "_truth_for_tid", lambda app, tid, cfg: ([], False))
    assert patch.open_live_count_without_verified_stuck(object(), "7") == 1


def test_platform_recovery_exclusivity_ignores_only_stuck_position(monkeypatch):
    cfg = _cfg()
    row = _stuck()
    monkeypatch.setattr(
        patch,
        "_PREV_PLATFORM_GATE",
        lambda app, cfg: (False, patch._RECOVERY_OPEN_BLOCK, {"profit_factor": Decimal("0.8")}, False),
    )
    monkeypatch.setattr(patch, "_global_snapshot", lambda app, cfg: ([('7', row)], [], True))
    monkeypatch.setattr(patch, "_notify_owner_resolution", lambda *args, **kwargs: None)

    ok, reason, _metrics, recovery = patch.platform_amount_gate_without_stuck_freeze(object(), cfg)
    assert ok is True
    assert recovery is True
    assert "LIQUIDITY_STUCK" in reason
    assert "OPEN/exposure" in reason


def test_platform_pf_cooldown_is_never_bypassed(monkeypatch):
    cfg = _cfg()
    row = _stuck()
    original = (False, "platform realised profit amount is below required target; recovery cooldown 120 min", {}, False)
    monkeypatch.setattr(patch, "_PREV_PLATFORM_GATE", lambda app, cfg: original)
    monkeypatch.setattr(patch, "_global_snapshot", lambda app, cfg: ([('7', row)], [], True))
    assert patch.platform_amount_gate_without_stuck_freeze(object(), cfg) == original


def test_active_nonstuck_position_still_blocks(monkeypatch):
    cfg = _cfg()
    stuck = _stuck()
    active = _stuck(position_id="pos-2", liquidity_state="OPEN", liquidity_attempts=0)
    original = (False, patch._RECOVERY_OPEN_BLOCK, {}, False)
    monkeypatch.setattr(patch, "_PREV_PLATFORM_GATE", lambda app, cfg: original)
    monkeypatch.setattr(patch, "_global_snapshot", lambda app, cfg: ([('7', stuck)], [('7', active)], True))
    assert patch.platform_amount_gate_without_stuck_freeze(object(), cfg) == original


def test_systemic_breaker_caps_multiple_stuck_positions(monkeypatch):
    cfg = _cfg()
    rows = [(str(i), _stuck(position_id=f"pos-{i}")) for i in range(4)]
    monkeypatch.setattr(
        patch,
        "_PREV_PLATFORM_GATE",
        lambda app, cfg: (False, patch._RECOVERY_OPEN_BLOCK, {}, False),
    )
    monkeypatch.setattr(patch, "_global_snapshot", lambda app, cfg: (rows, [], True))
    ok, reason, _metrics, recovery = patch.platform_amount_gate_without_stuck_freeze(object(), cfg)
    assert ok is False
    assert recovery is False
    assert "systemic liquidity safety breaker" in reason


def test_owner_notice_explains_force_exit_and_writeoff(monkeypatch):
    cfg = _cfg()
    row = _stuck(position_id="07d9f95e7dbb77288b2d4abca53e3949")
    row["mint"] = "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV"
    captured = []
    monkeypatch.setattr(patch, "_notice_due", lambda *args, **kwargs: True)
    monkeypatch.setattr(patch, "_mark_notice", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        patch,
        "_position_detail",
        lambda app, pid: {
            "entry_cost_sol": "0.0005",
            "entry_ts": int(time.time()) - 600,
            "leader_wallet": "Leader1111111111111111111111111111111111111",
            "leader_rank": 1,
            "leader_buy_signature": "BuySignature111111111111111111111111111111111111",
        },
    )
    monkeypatch.setattr(patch._live, "_notify", lambda app, tid, message: captured.append(message))
    monkeypatch.setattr(patch._emergency, "_manual_force_limit", lambda cfg: Decimal("9500"))

    patch._notify_owner_resolution(object(), "676", row, cfg)
    assert len(captured) == 1
    msg = captured[0]
    assert "strategy will <b>continue" in msg.lower()
    assert "/solanaforceexit 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
    assert "/solanawriteoff 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
    assert "sends <b>no transaction</b>" in msg
    assert "leaves the tokens untouched" in msg
    assert "Entry cost still at risk" in msg
