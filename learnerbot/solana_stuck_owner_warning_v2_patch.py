from __future__ import annotations

import html
import re
import time
from contextlib import closing
from decimal import Decimal

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_patch as _live
from . import solana_sibot as _sol
from . import telegram_solana_force_exit_patch as _force

# Presentation/reminder overlay only. Trading hooks, execution ceilings, signing,
# reserve, simulation and quality gates are not changed here.
_sol.DEFAULTS.update({
    "live_liquidity_stuck_owner_notice_minutes": (
        "60",
        "Repeat the full owner decision warning this often while a verified Solana position remains LIQUIDITY_STUCK",
    ),
})

_PREV_NOTIFY = _live._notify
_EMERGENCY_PREFIX = "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>"
_PRICE_IMPACT_RE = re.compile(r"price impact\s+([0-9]+(?:\.[0-9]+)?)\s+bps", re.I)


def _notice_due(app, tid: str, pid: str, cfg: dict) -> bool:
    minutes = max(15, min(1440, _sol._int(cfg.get("live_liquidity_stuck_owner_notice_minutes"), 60)))
    now = int(time.time())
    try:
        with closing(_sol.connect(app)) as conn:
            raw = _sol._state(conn, _stuck._notice_key(str(tid), str(pid)), "0") or "0"
        last = int(raw)
    except Exception:
        last = 0
    return last <= 0 or now - last >= minutes * 60


def _sol_usd_price() -> Decimal | None:
    try:
        from . import telegram_solana_everywhere_compat_patch as _compat
        value = _compat._sol_price_usd()
        price = Decimal(str(value)) if value is not None else Decimal(0)
        return price if price > 0 else None
    except Exception:
        return None


def _usd_text(sol_amount: Decimal) -> str:
    price = _sol_usd_price()
    if price is None:
        return "USD unavailable"
    usd = max(Decimal(0), Decimal(sol_amount)) * price
    if usd >= Decimal("0.01"):
        return f"≈ ${usd:,.2f}"
    return f"≈ ${usd:,.6f}".rstrip("0").rstrip(".")


def _latest_guard(app, tid: str, mint: str) -> str:
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                """SELECT reason FROM live_execution_guard_events
                   WHERE telegram_id=? AND action='SELL' AND input_mint=?
                   ORDER BY created_at DESC, event_id DESC LIMIT 1""",
                (str(tid), str(mint)),
            ).fetchone()
        return str(row["reason"] or "") if row else ""
    except Exception:
        return ""


def _cause(guard: str, attempts: int) -> tuple[str, str]:
    text = str(guard or "")
    lower = text.lower()
    match = _PRICE_IMPACT_RE.search(text)
    impact = Decimal(match.group(1)) if match else None
    if impact is not None and impact >= Decimal("9000"):
        return (
            "Severe liquidity collapse / rug-like condition",
            "The latest route implies roughly 90–100% price impact. That is consistent with liquidity being drained and can occur in a rug pull, but price impact alone does not prove malicious conduct. The bot therefore treats it as an unsafely illiquid market, not as a proven rug.",
        )
    if "failed to get quotes" in lower or "no safe slice quote" in lower or "no executable quote" in lower:
        return (
            "No executable exit route",
            "Jupiter cannot currently produce a usable route. Possible causes include drained/removed liquidity, a disabled pool, route unavailability, or token-specific transfer/trading mechanics. A rug is possible but not proved from this evidence alone.",
        )
    if "quoted price impact" in lower or "exceeds" in lower:
        return (
            "Liquidity too shallow",
            "A route exists, but selling would exceed the platform's loss/impact ceiling. This normally means available exit liquidity is far too small for the position at present.",
        )
    if "net proceeds after fees" in lower:
        return (
            "Economically uneconomic exit",
            "A route exists, but expected proceeds after fees are below the emergency minimum. The bot is refusing to spend transaction cost for effectively no recovery.",
        )
    return (
        "Persistent exit-liquidity failure",
        f"The position has failed {attempts} complete safe-exit round(s). The available evidence is insufficient to label the event a rug; the safe conclusion is that the token is presently not economically exit-able within the configured protection limits.",
    )


def _position(app, tid: str, pid: str) -> dict:
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE position_id=? AND telegram_id=? AND mode='LIVE'",
                (str(pid), str(tid)),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _truth_row(app, tid: str, pid: str, cfg: dict) -> dict:
    rows, proven = _stuck._truth_for_tid(app, str(tid), cfg)
    if not proven:
        return {}
    for row in rows:
        if str(row.get("position_id") or "") == str(pid):
            return dict(row)
    return {}


def _build_owner_message(app, tid: str, position: dict, cfg: dict) -> str:
    pid = str(position.get("position_id") or "")
    detail = _stuck._position_detail(app, pid)
    mint = str(position.get("mint") or detail.get("mint") or "")
    entry_cost = max(Decimal(0), _sol._dec(detail.get("entry_cost_sol"), 0))
    entry_time = _stuck._iso(detail.get("entry_ts") or position.get("entry_ts"))
    recorded = str(position.get("recorded_raw") or detail.get("token_amount_raw") or "0")
    verified = str(position.get("verified_balance_raw") or "0")
    attempts = max(0, _sol._int(position.get("liquidity_attempts"), 0))
    slices = "/".join(position.get("safe_slice_percentages") or []) or "100/75/50/25/10/5/2/1"
    auto_limit = Decimal(str(position.get("emergency_limit_bps") or "500")) / Decimal(100)
    manual_limit = _emergency._manual_force_limit(cfg) / Decimal(100)
    guard = _latest_guard(app, str(tid), mint)
    label, explanation = _cause(guard, attempts)
    risk_usd = _usd_text(entry_cost)

    return (
        "🚨 <b>SOLANA LIQUIDITY_STUCK — TRADING CONTINUES</b>\n"
        "✅ <b>Other eligible Solana mints are NOT blocked by this position.</b>\n"
        "⛔ The same mint remains blocked from re-entry while this unresolved inventory exists.\n\n"
        f"<b>Position</b>\n• ID: <code>{html.escape(pid)}</code>\n"
        f"• Mint: <code>{html.escape(mint)}</code>\n"
        f"• Entry: <b>{html.escape(entry_time)}</b>\n"
        f"• Remaining tracked cost at risk: <b>{entry_cost:.9f} SOL ({html.escape(risk_usd)})</b>\n"
        f"• If written off now at zero recovery, estimated accounting loss: <b>{entry_cost:.9f} SOL ({html.escape(risk_usd)})</b>\n"
        f"• Recorded token raw: <b>{html.escape(recorded)}</b>\n"
        f"• Verified wallet token raw: <b>{html.escape(verified)}</b>\n\n"
        f"<b>What appears to have happened</b>\n<b>{html.escape(label)}</b>\n{html.escape(explanation)}\n"
        + (f"Latest guard: <code>{html.escape(guard[:500])}</code>\n" if guard else "")
        + f"Failed complete safe-exit rounds: <b>{attempts}</b>\n"
        f"Safe slices tested: <b>{html.escape(slices)}%</b>\n"
        f"Automatic impact+slippage ceiling: <b>{auto_limit:.2f}%</b>\n\n"
        "<b>Your options</b>\n"
        "1️⃣ <b>Keep automatic recovery</b> — do nothing. The bot keeps retrying safe slices and continues evaluating/trading other eligible mints.\n\n"
        "2️⃣ <b>Force exit</b> — knowingly accept a much larger realised loss if an executable route exists. A literal ~100% impact route is still refused.\n"
        f"<code>/solanaforceexit {html.escape(pid)} CONFIRM</code>\n"
        f"Manual hard ceiling: <b>{manual_limit:.0f}%</b> impact+slippage.\n\n"
        "3️⃣ <b>Write off</b> — accounting close at zero recovery. No SELL, burn or transfer is sent; the token stays in the wallet. The remaining tracked cost is recognised as realised loss and the position stops generating exit retries. Any later separately proven recovery can be reconciled as recovery rather than pretending a sale occurred.\n"
        f"<code>/solanawriteoff {html.escape(pid)} CONFIRM</code>\n\n"
        "🔔 This full warning repeats approximately hourly while the position remains verified LIQUIDITY_STUCK."
    )


def notify_owner_resolution_v2(app, tid: str, position: dict, cfg: dict) -> None:
    pid = str(position.get("position_id") or "")
    if not pid or not _notice_due(app, str(tid), pid, cfg):
        return
    message = _build_owner_message(app, str(tid), dict(position), cfg)
    try:
        # Use the pre-v2 notifier so this decision card cannot recurse through the
        # emergency-prefix interception below.
        _PREV_NOTIFY(app, str(tid), message)
        _stuck._mark_notice(app, str(tid), pid)
    except Exception:
        pass


def _position_id_from_emergency(text: str) -> str:
    match = re.search(r"Position: <code>(.*?)</code>", str(text or ""))
    return html.unescape(match.group(1)).strip() if match else ""


def notify_with_stuck_decision(app, tid, text, *args, **kwargs):
    raw = str(text or "")
    if not raw.startswith(_EMERGENCY_PREFIX):
        return _PREV_NOTIFY(app, tid, raw, *args, **kwargs)

    pid = _position_id_from_emergency(raw)
    if not pid:
        return _PREV_NOTIFY(app, tid, raw, *args, **kwargs)

    cfg = _stuck._cfg(app)
    truth = _truth_row(app, str(tid), pid, cfg)
    if not truth or not _stuck._is_verified_stuck(truth, cfg):
        # Before chronic stuck status is proved, retain the concise per-attempt
        # emergency warning so the operator knows no transaction was broadcast.
        return _PREV_NOTIFY(app, tid, raw, *args, **kwargs)

    # Once verified chronic, avoid repetitive retry spam. The full decision card
    # becomes the canonical warning and repeats on the configured hourly cadence.
    if _notice_due(app, str(tid), pid, cfg):
        formatted = _force._format_emergency_liquidity_notice(raw)
        decision = _build_owner_message(app, str(tid), truth, cfg)
        combined = formatted + "\n\n" + decision
        try:
            result = _force._ORIGINAL_LIVE_NOTIFY(app, str(tid), combined, *args, **kwargs)
            _stuck._mark_notice(app, str(tid), pid)
            return result
        except Exception:
            return None
    return None


def install() -> None:
    if getattr(_live, "_stuck_owner_warning_v2_installed", False):
        return
    _stuck._notify_owner_resolution = notify_owner_resolution_v2
    _live._notify = notify_with_stuck_decision
    _emergency._live._notify = notify_with_stuck_decision
    _live._stuck_owner_warning_v2_installed = True
    print(
        "[solana-stuck-owner-warning-v2] other_mints_nonblocking=true same_mint_blocked=true "
        "hourly_owner_warning=true usd_loss=true cause_classification=true force_exit=true writeoff=true"
    )


install()
