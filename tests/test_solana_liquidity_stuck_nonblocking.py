from __future__ import annotations

import subprocess
import sys


def test_liquidity_stuck_nonblocking_final_overlay():
    script = r'''
import time
from decimal import Decimal

from learnerbot import solana_liquidity_stuck_nonblocking_patch as patch
from learnerbot import solana_live_patch as live
from learnerbot import solana_positive_edge_entry_gate_patch as edge
from learnerbot import solana_sibot as sol

cfg = {
    "live_liquidity_stuck_nonblocking": "true",
    "live_liquidity_stuck_min_attempts": "2",
    "live_liquidity_stuck_min_seconds": "60",
    "live_liquidity_stuck_max_quarantined": "3",
    "live_liquidity_stuck_owner_notice_hours": "12",
}

def stuck(now=None, **overrides):
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

# Production hook identities.
assert live._open_live_count is patch.open_live_count_without_verified_stuck
assert edge._platform_amount_gate is patch.platform_amount_gate_without_stuck_freeze
assert sol.monitor_positions is patch.monitor_positions_with_stuck_owner_resolution

# Only a verified, durable, positive-balance stuck position can free capacity.
now = 2_000_000_000
assert patch._is_verified_stuck(stuck(now), cfg, now=now)
assert not patch._is_verified_stuck(stuck(now, verified=False), cfg, now=now)
assert not patch._is_verified_stuck(stuck(now, verified_balance_raw="0"), cfg, now=now)
assert not patch._is_verified_stuck(stuck(now, liquidity_attempts=1), cfg, now=now)
assert not patch._is_verified_stuck(stuck(now, liquidity_first_blocked_epoch=now - 20), cfg, now=now)
assert not patch._is_verified_stuck(stuck(now, liquidity_state="OPEN"), cfg, now=now)

# Capacity excludes verified stuck; uncertainty fails closed to the old count.
row = stuck()
patch._PREV_OPEN_COUNT = lambda app, tid: 1
patch._cfg = lambda app: cfg
patch._truth_for_tid = lambda app, tid, cfg: ([row], True)
assert patch.open_live_count_without_verified_stuck(object(), "7") == 0
patch._truth_for_tid = lambda app, tid, cfg: ([], False)
assert patch.open_live_count_without_verified_stuck(object(), "7") == 1

# Only recovery exclusivity is relaxed. PF cooldown remains authoritative.
real_owner_notify = patch._notify_owner_resolution
patch._PREV_PLATFORM_GATE = lambda app, cfg: (
    False, patch._RECOVERY_OPEN_BLOCK, {"profit_factor": Decimal("0.8")}, False
)
patch._global_snapshot = lambda app, cfg: ([('7', row)], [], True)
patch._notify_owner_resolution = lambda *args, **kwargs: None
ok, reason, metrics, recovery = patch.platform_amount_gate_without_stuck_freeze(object(), cfg)
assert ok is True and recovery is True
assert "LIQUIDITY_STUCK" in reason and "OPEN/exposure" in reason

cooldown = (False, "platform realised profit amount is below required target; recovery cooldown 120 min", {}, False)
patch._PREV_PLATFORM_GATE = lambda app, cfg: cooldown
assert patch.platform_amount_gate_without_stuck_freeze(object(), cfg) == cooldown

# A normal active position still blocks; four stuck positions trip systemic breaker
# even if the underlying platform PF gate would otherwise pass.
active = stuck(position_id="pos-2", liquidity_state="OPEN", liquidity_attempts=0)
original = (False, patch._RECOVERY_OPEN_BLOCK, {}, False)
patch._PREV_PLATFORM_GATE = lambda app, cfg: original
patch._global_snapshot = lambda app, cfg: ([('7', row)], [('7', active)], True)
assert patch.platform_amount_gate_without_stuck_freeze(object(), cfg) == original
patch._PREV_PLATFORM_GATE = lambda app, cfg: (True, "healthy platform PF", {}, False)
patch._global_snapshot = lambda app, cfg: ([(str(i), stuck(position_id=f"pos-{i}")) for i in range(4)], [], True)
ok, reason, metrics, recovery = patch.platform_amount_gate_without_stuck_freeze(object(), cfg)
assert ok is False and recovery is False and "systemic liquidity safety breaker" in reason

# Detailed owner message must explain both human-only resolution paths.
patch._notify_owner_resolution = real_owner_notify
row = stuck(position_id="07d9f95e7dbb77288b2d4abca53e3949")
row["mint"] = "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV"
captured = []
patch._notice_due = lambda *args, **kwargs: True
patch._mark_notice = lambda *args, **kwargs: None
patch._position_detail = lambda app, pid: {
    "entry_cost_sol": "0.0005",
    "entry_ts": int(time.time()) - 600,
    "leader_wallet": "Leader1111111111111111111111111111111111111",
    "leader_rank": 1,
    "leader_buy_signature": "BuySignature111111111111111111111111111111111111",
}
patch._live._notify = lambda app, tid, message: captured.append(message)
patch._emergency._manual_force_limit = lambda cfg: Decimal("9500")
patch._notify_owner_resolution(object(), "676", row, cfg)
assert len(captured) == 1
msg = captured[0]
assert "strategy will <b>continue" in msg.lower()
assert "/solanaforceexit 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
assert "/solanawriteoff 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
assert "sends <b>no transaction</b>" in msg
assert "leaves the tokens untouched" in msg
assert "Entry cost still at risk" in msg

print("LIQUIDITY_STUCK_NONBLOCKING_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "LIQUIDITY_STUCK_NONBLOCKING_OK" in result.stdout
