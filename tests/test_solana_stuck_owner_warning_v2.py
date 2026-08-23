from __future__ import annotations

import subprocess
import sys


def test_stuck_owner_warning_v2_overlay():
    script = r'''
from decimal import Decimal

from learnerbot import solana_stuck_owner_warning_v2_patch as warning
from learnerbot import solana_liquidity_stuck_nonblocking_patch as stuck
from learnerbot import solana_live_patch as live
from learnerbot import solana_positive_edge_entry_gate_patch as edge
from learnerbot import solana_emergency_liquidity_unwind_patch as emergency
from learnerbot import telegram_solana_force_exit_patch as force

cfg = {
    "live_liquidity_stuck_nonblocking": "true",
    "live_liquidity_stuck_min_attempts": "2",
    "live_liquidity_stuck_min_seconds": "60",
    "live_liquidity_stuck_owner_notice_minutes": "60",
}
row = {
    "position_id": "07d9f95e7dbb77288b2d4abca53e3949",
    "mint": "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV",
    "recorded_raw": "87400222",
    "verified": True,
    "verified_balance_raw": "87405554",
    "liquidity_state": "LIQUIDITY_STUCK",
    "liquidity_attempts": 3,
    "liquidity_first_blocked_epoch": 1,
    "safe_slice_percentages": ["100", "75", "50", "25", "10", "5", "2", "1"],
    "emergency_limit_bps": "500",
}

# Presentation overlay must not disturb the audited non-blocking trading hooks.
assert live._open_live_count is stuck.open_live_count_without_verified_stuck
assert edge._platform_amount_gate is stuck.platform_amount_gate_without_stuck_freeze
assert warning._stuck._notify_owner_resolution is warning.notify_owner_resolution_v2
assert live._notify is warning.notify_with_stuck_decision

warning._sol_usd_price = lambda: Decimal("200")
warning._latest_guard = lambda app, tid, mint: (
    "quoted price impact 10000.00 bps + slippage 50 bps = 10050.00 bps exceeds 500 bps"
)
stuck._position_detail = lambda app, pid: {
    "position_id": pid,
    "mint": row["mint"],
    "entry_cost_sol": "0.005",
    "entry_ts": 2_000_000_000,
    "token_amount_raw": row["recorded_raw"],
}
emergency._manual_force_limit = lambda cfg: Decimal("9500")

msg = warning._build_owner_message(object(), "5923828381", row, cfg)
assert "TRADING CONTINUES" in msg
assert "Other eligible Solana mints are NOT blocked" in msg
assert "same mint remains blocked" in msg
assert "0.005000000 SOL (≈ $1.00)" in msg
assert "estimated accounting loss" in msg
assert "Severe liquidity collapse / rug-like condition" in msg
assert "does not prove malicious conduct" in msg
assert "/solanaforceexit 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
assert "/solanawriteoff 07d9f95e7dbb77288b2d4abca53e3949 CONFIRM" in msg
assert "repeats approximately hourly" in msg

# Once the position is formally stuck, the next emergency warning is composed
# with the full owner decision card instead of sending a separate incomplete alert.
captured = []
warning._truth_row = lambda app, tid, pid, cfg: dict(row)
stuck._is_verified_stuck = lambda position, cfg: True
warning._notice_due = lambda app, tid, pid, cfg: True
force._format_emergency_liquidity_notice = lambda text: "FORMATTED EMERGENCY"
force._ORIGINAL_LIVE_NOTIFY = lambda app, tid, text, *a, **k: captured.append(text)
stuck._mark_notice = lambda *args, **kwargs: None
raw = (
    "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>\n"
    "Reason: <code>SOLANA_STOP_LOSS</code>\n"
    "Position: <code>07d9f95e7dbb77288b2d4abca53e3949</code>\n"
    "Hard impact+slippage ceiling: <b>5.00%</b>\n"
    "Last guard: <code>quoted price impact 10000.00 bps exceeds 500 bps</code>\n"
    "Automatic retry: <b>60s</b> (liquidity attempt 3)."
)
warning.notify_with_stuck_decision(object(), "5923828381", raw)
assert len(captured) == 1
assert "FORMATTED EMERGENCY" in captured[0]
assert "TRADING CONTINUES" in captured[0]
assert "/solanawriteoff" in captured[0]

# Between hourly decision reminders, chronic retry spam is suppressed.
captured.clear()
warning._notice_due = lambda app, tid, pid, cfg: False
warning.notify_with_stuck_decision(object(), "5923828381", raw)
assert captured == []

print("SOLANA_STUCK_OWNER_WARNING_V2_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SOLANA_STUCK_OWNER_WARNING_V2_OK" in result.stdout
