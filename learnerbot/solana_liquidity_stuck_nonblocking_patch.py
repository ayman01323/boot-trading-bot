from __future__ import annotations

import html
import time
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_live_patch as _live
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol
from . import solana_trade_gate_truth_patch as _truth

# A genuinely liquidity-stuck position is real exposure and must remain OPEN in
# accounting, but it must not freeze the whole Solana strategy forever. This
# layer removes only a *verified, durable* liquidity-stuck position from the
# entry-capacity / recovery-canary exclusivity count. It does not remove the DB
# row, does not reduce recorded exposure, does not permit a same-mint re-entry,
# and does not bypass funding, leader-quality, reverse-liquidity, reserve,
# simulation, signing, price-impact or platform-PF cooldown guards.
_HARD_MAX_QUARANTINED = 3
_RECOVERY_OPEN_BLOCK = "platform amount gate is in recovery mode and another LIVE position is still open"
_RECOVERY_WAIT_PREFIX = "one recovery LIVE BUY already executed; waiting for that position to close before re-evaluation"

_sol.DEFAULTS.update({
    "live_liquidity_stuck_nonblocking": (
        "true",
        "Allow other-mint Solana opportunities to continue when a verified position is durably LIQUIDITY_STUCK",
    ),
    "live_liquidity_stuck_min_attempts": (
        "2",
        "Failed complete emergency-liquidity rounds required before an OPEN position is treated as capacity-nonblocking",
    ),
    "live_liquidity_stuck_min_seconds": (
        "60",
        "Minimum time since the first failed emergency-liquidity round before capacity is freed",
    ),
    "live_liquidity_stuck_max_quarantined": (
        "3",
        "Maximum simultaneous verified liquidity-stuck positions allowed before the systemic safety breaker blocks new Solana entries",
    ),
    "live_liquidity_stuck_owner_notice_hours": (
        "12",
        "Minimum hours between detailed owner-resolution reminders for the same liquidity-stuck position",
    ),
})

_PREV_OPEN_COUNT = _live._open_live_count
_PREV_PLATFORM_GATE = _edge._platform_amount_gate
_PREV_MONITOR_POSITIONS = _sol.monitor_positions


def _cfg(app) -> dict:
    try:
        return dict(_sol.settings(app))
    except Exception:
        return {}


def _enabled(cfg: dict) -> bool:
    return _sol._bool(cfg.get("live_liquidity_stuck_nonblocking"), True)


def _minimum_attempts(cfg: dict) -> int:
    return max(2, min(20, _sol._int(cfg.get("live_liquidity_stuck_min_attempts"), 2)))


def _minimum_seconds(cfg: dict) -> int:
    return max(30, min(3600, _sol._int(cfg.get("live_liquidity_stuck_min_seconds"), 60)))


def _max_quarantined(cfg: dict) -> int:
    # This setting may make the breaker stricter, never looser than the hard cap.
    return max(1, min(_HARD_MAX_QUARANTINED, _sol._int(cfg.get("live_liquidity_stuck_max_quarantined"), 3)))


def _is_verified_stuck(position: dict, cfg: dict, now: int | None = None) -> bool:
    if not _enabled(cfg):
        return False
    now = int(now or time.time())
    if str(position.get("liquidity_state") or "").upper() != "LIQUIDITY_STUCK":
        return False
    if not bool(position.get("verified")):
        return False
    try:
        if int(position.get("verified_balance_raw") or 0) <= 0:
            return False
    except Exception:
        return False
    attempts = max(0, _sol._int(position.get("liquidity_attempts"), 0))
    first = max(0, _sol._int(position.get("liquidity_first_blocked_epoch"), 0))
    if attempts < _minimum_attempts(cfg) or first <= 0:
        return False
    return now - first >= _minimum_seconds(cfg)


def _truth_for_tid(app, tid: str, cfg: dict) -> tuple[list[dict], bool]:
    """Return OPEN LIVE truth and whether every DB row was represented.

    The verification helper itself fails closed on wallet/RPC uncertainty. We also
    compare its output cardinality to the DB so a reporting failure can never free
    capacity accidentally.
    """
    try:
        with closing(_sol.connect(app)) as conn:
            expected = int(conn.execute(
                "SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
                (str(tid),),
            ).fetchone()["n"])
    except Exception:
        return [], False
    try:
        rows = list(_truth._open_live_truth(app, str(tid)) or [])
    except Exception:
        return [], False
    return rows, len(rows) == expected


def _global_snapshot(app, cfg: dict) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]], bool]:
    try:
        with closing(_sol.connect(app)) as conn:
            tids = [
                str(r["telegram_id"] or "")
                for r in conn.execute(
                    "SELECT DISTINCT telegram_id FROM positions WHERE status='OPEN' AND mode='LIVE'"
                ).fetchall()
                if str(r["telegram_id"] or "").strip()
            ]
    except Exception:
        return [], [], False

    stuck: list[tuple[str, dict]] = []
    active: list[tuple[str, dict]] = []
    for tid in tids:
        rows, proven = _truth_for_tid(app, tid, cfg)
        if not proven:
            return [], [], False
        for row in rows:
            if _is_verified_stuck(row, cfg):
                stuck.append((tid, row))
            else:
                active.append((tid, row))
    return stuck, active, True


def open_live_count_without_verified_stuck(app, tid) -> int:
    """Capacity count only; DB OPEN/exposure truth is never changed.

    Run the existing verified-zero reconciler first so truly empty stale rows can
    still become RECONCILE_REQUIRED. If the remaining rows cannot be proven, keep
    the previous fail-closed count.
    """
    base = int(_PREV_OPEN_COUNT(app, tid))
    if base <= 0:
        return 0
    cfg = _cfg(app)
    rows, proven = _truth_for_tid(app, str(tid), cfg)
    if not proven:
        return base
    active = sum(1 for row in rows if not _is_verified_stuck(row, cfg))
    return max(0, active)


def platform_amount_gate_without_stuck_freeze(app, cfg: dict):
    ok, reason, metrics, recovery = _PREV_PLATFORM_GATE(app, cfg)
    if ok or not _enabled(cfg):
        return ok, reason, metrics, recovery

    stuck, active, proven = _global_snapshot(app, cfg)
    if not proven or not stuck or active:
        return ok, reason, metrics, recovery

    maximum = _max_quarantined(cfg)
    if len(stuck) > maximum:
        return (
            False,
            f"systemic liquidity safety breaker: {len(stuck)} verified LIQUIDITY_STUCK positions exceed hard concurrent limit {maximum}",
            metrics,
            False,
        )

    text = str(reason or "")
    if text != _RECOVERY_OPEN_BLOCK and not text.startswith(_RECOVERY_WAIT_PREFIX):
        # Profit-factor cooldown, mint quarantine and every other platform guard
        # remain authoritative. Only stuck-position exclusivity is relaxed.
        return ok, reason, metrics, recovery

    for tid, position in stuck:
        _notify_owner_resolution(app, tid, position, cfg)
    return (
        True,
        f"{len(stuck)} verified LIQUIDITY_STUCK position(s) quarantined from recovery exclusivity; trapped capital remains OPEN/exposure and same-mint entries remain blocked",
        metrics,
        True,
    )


def _position_detail(app, position_id: str) -> dict:
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute("SELECT * FROM positions WHERE position_id=?", (str(position_id),)).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _iso(ts) -> str:
    try:
        value = int(ts or 0)
    except Exception:
        value = 0
    if value <= 0:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _notice_key(tid: str, pid: str) -> str:
    return f"solana_liquidity_stuck_owner_notice:{tid}:{pid}"


def _notice_due(app, tid: str, pid: str, cfg: dict) -> bool:
    hours = max(1, min(168, _sol._int(cfg.get("live_liquidity_stuck_owner_notice_hours"), 12)))
    now = int(time.time())
    try:
        with closing(_sol.connect(app)) as conn:
            raw = _sol._state(conn, _notice_key(tid, pid), "0") or "0"
        last = int(raw)
    except Exception:
        last = 0
    return last <= 0 or now - last >= hours * 3600


def _mark_notice(app, tid: str, pid: str) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _sol._set_state(conn, _notice_key(tid, pid), str(int(time.time())))
    except Exception:
        pass


def _notify_owner_resolution(app, tid: str, position: dict, cfg: dict) -> None:
    pid = str(position.get("position_id") or "")
    if not pid or not _notice_due(app, str(tid), pid, cfg):
        return
    detail = _position_detail(app, pid)
    mint = str(position.get("mint") or detail.get("mint") or "")
    entry_cost = _sol._dec(detail.get("entry_cost_sol"), 0)
    entry_time = _iso(detail.get("entry_ts") or position.get("entry_ts"))
    leader = str(detail.get("leader_wallet") or "")
    leader_rank = _sol._int(detail.get("leader_rank"), 0)
    buy_sig = str(detail.get("leader_buy_signature") or "")
    recorded = str(position.get("recorded_raw") or detail.get("token_amount_raw") or "0")
    verified = str(position.get("verified_balance_raw") or "0")
    attempts = max(0, _sol._int(position.get("liquidity_attempts"), 0))
    first = _iso(position.get("liquidity_first_blocked_epoch"))
    slices = "/".join(position.get("safe_slice_percentages") or []) or "100/75/50/25/10/5/2/1"
    auto_limit = Decimal(str(position.get("emergency_limit_bps") or "500")) / Decimal(100)
    manual_limit = _emergency._manual_force_limit(cfg) / Decimal(100)

    message = (
        "🚨 <b>Solana LIQUIDITY_STUCK — owner decision available</b>\n"
        "The strategy will <b>continue evaluating and trading other eligible mints</b>; this trapped position no longer freezes Solana entry capacity.\n\n"
        f"Position: <code>{html.escape(pid)}</code>\n"
        f"Mint: <code>{html.escape(mint)}</code>\n"
        f"Entry: <b>{html.escape(entry_time)}</b>\n"
        f"Entry cost still at risk: <b>{entry_cost:.9f} SOL</b>\n"
        f"Recorded token raw: <b>{html.escape(recorded)}</b>\n"
        f"Verified wallet token raw: <b>{html.escape(verified)}</b>\n"
        + (f"Copied leader: <code>{html.escape(leader)}</code> • rank <b>{leader_rank}</b>\n" if leader else "")
        + (f"Leader BUY signature: <code>{html.escape(buy_sig)}</code>\n" if buy_sig else "")
        + f"Liquidity trouble first recorded: <b>{html.escape(first)}</b>\n"
        f"Failed safe exit rounds: <b>{attempts}</b>\n"
        f"Automatic slices tested: <b>{html.escape(slices)}%</b>\n"
        f"Automatic impact+slippage ceiling: <b>{auto_limit:.2f}%</b>\n\n"
        "<b>What happened</b>\n"
        "The token balance is still present, but Jupiter has not produced an exit that clears the bot's safe liquidity ceiling. The automatic path keeps retrying and will not throw the token away through a near-total-impact quote. The position remains OPEN in risk/exposure accounting and the same mint cannot be bought again.\n\n"
        "<b>Your choices</b>\n"
        "1️⃣ <b>Keep trying automatically</b> — do nothing. The bot keeps retrying safe slices while other eligible Solana trades may continue.\n\n"
        "2️⃣ <b>Force an owner-approved exit</b> — this can accept a much larger realised loss, but still refuses a literal ~100% impact quote.\n"
        f"<code>/solanaforceexit {html.escape(pid)} CONFIRM</code>\n"
        f"Manual hard ceiling: up to <b>{manual_limit:.0f}%</b> impact+slippage.\n\n"
        "3️⃣ <b>Write it off in accounting</b> — use this only if you accept that the remaining token may be economically unrecoverable. This sends <b>no transaction</b>, leaves the tokens untouched in your wallet, records the remaining entry cost as a realised loss, and closes the bot's accounting position. It does not recover money or burn the token.\n"
        f"<code>/solanawriteoff {html.escape(pid)} CONFIRM</code>\n\n"
        "⚠️ A liquidity write-off is an accounting decision, not a sale."
    )
    try:
        _live._notify(app, str(tid), message)
        _mark_notice(app, str(tid), pid)
    except Exception:
        pass


def monitor_positions_with_stuck_owner_resolution(app):
    result = _PREV_MONITOR_POSITIONS(app)
    try:
        cfg = _cfg(app)
        stuck, _active, proven = _global_snapshot(app, cfg)
        if proven:
            for tid, position in stuck:
                _notify_owner_resolution(app, tid, position, cfg)
    except Exception:
        pass
    return result


def install() -> None:
    if getattr(_sol, "_liquidity_stuck_nonblocking_installed", False):
        return
    _live._open_live_count = open_live_count_without_verified_stuck
    _edge._platform_amount_gate = platform_amount_gate_without_stuck_freeze
    _sol.monitor_positions = monitor_positions_with_stuck_owner_resolution
    _sol._liquidity_stuck_nonblocking_installed = True
    print(
        "[solana-liquidity-stuck-nonblocking] verified_only=true min_attempts=2 min_seconds=60 "
        "same_mint_blocked=true exposure_open=true owner_resolution=true systemic_max=3"
    )


install()
