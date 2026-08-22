from __future__ import annotations

import html
import json
import time
from contextlib import closing
from decimal import Decimal

import requests

from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_sibot as _sol

# Every existing liquidity check in this codebase runs either at entry (round-trip
# quote, positive-executable-edge) or at the moment an exit is actually attempted
# (the emergency-liquidity ceiling in solana_emergency_liquidity_unwind_patch.py).
# Nothing looks at liquidity on an already-OPEN position in between, so a token can
# go from healthy to a drained/rugged pool over hours and the bot only discovers it
# the moment it tries to sell -- by which point it is too late to avoid, only to
# refuse.
#
# This layer adds a periodic, read-only liquidity check on open LIVE positions and
# sends an early Telegram warning while the position may still be exitable within
# the *ordinary* execution guard. It is purely observational: it never signs,
# broadcasts, closes, resizes or otherwise touches a position. It cannot create a
# trade and it cannot bypass or weaken any existing execution/liquidity/simulation
# gate -- it can only tell a human sooner than they would otherwise find out.
_sol.DEFAULTS.update({
    "live_liquidity_health_check_seconds": (
        "900",
        "Minimum seconds between liquidity re-quotes for the same open LIVE Solana position",
    ),
    "live_liquidity_warning_combined_bps": (
        "150",
        "Quoted price-impact+slippage bps on an open position's full exit that triggers an early liquidity warning",
    ),
    "live_liquidity_warning_repeat_hours": (
        "4",
        "Minimum hours between repeat liquidity warnings for the same still-at-risk position",
    ),
})

_HEADERS = {"User-Agent": "BOOT-SiBot-Solana-LIVE/1.1"}


def _d(v, default="0") -> Decimal:
    return _sol._dec(v, default)


def _quote_only(input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict:
    """Read-only Jupiter quote. No taker/wallet is required and nothing is signed
    or broadcast -- this is the same quote-only request the emergency-liquidity
    layer uses to test a slice before ever attempting a real close."""
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        "slippageBps": str(int(slippage_bps)),
        "excludeRouters": "jupiterz",
    }
    r = requests.get(f"{_exec.JUPITER_BASE}/order", params=params, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _combined_impact_slippage_bps(quote: dict, slippage_bps: int) -> Decimal:
    if quote.get("priceImpact") is not None:
        impact = abs(_d(quote.get("priceImpact"), 0)) * Decimal(100)
    elif quote.get("priceImpactPct") is not None:
        impact = abs(_d(quote.get("priceImpactPct"), 0)) * Decimal(10_000)
    else:
        impact = Decimal(0)
    return impact + Decimal(max(0, int(slippage_bps)))


def _open_live_positions(app) -> list[dict]:
    with closing(_sol.connect(app)) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND mode='LIVE' ORDER BY updated_at"
        ).fetchall()]


def _due_for_check(app, position_id: str, interval_seconds: int) -> bool:
    key = f"liquidity_health_last_check:{position_id}"
    with closing(_sol.connect(app)) as conn:
        last = _sol._int(_sol._state(conn, key, "0"), 0)
    return int(time.time()) - last >= max(60, int(interval_seconds))


def _mark_checked(app, position_id: str) -> None:
    key = f"liquidity_health_last_check:{position_id}"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, key, str(int(time.time())))


def _due_for_alert(app, position_id: str, repeat_hours: int) -> bool:
    key = f"liquidity_health_last_alert:{position_id}"
    with closing(_sol.connect(app)) as conn:
        last = _sol._int(_sol._state(conn, key, "0"), 0)
    return int(time.time()) - last >= max(1, int(repeat_hours)) * 3600


def _mark_alerted(app, position_id: str) -> None:
    key = f"liquidity_health_last_alert:{position_id}"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, key, str(int(time.time())))


def _short(v: str) -> str:
    v = str(v or "")
    return v if len(v) <= 18 else f"{v[:8]}…{v[-6:]}"


def _warn_position(app, position: dict, combined_bps: Decimal, warning_bps: Decimal) -> None:
    tid = str(position.get("telegram_id") or "")
    pid = str(position.get("position_id") or "")
    mint = str(position.get("mint") or "")
    _live._notify(
        app,
        tid,
        "\n".join([
            "⚠️ <b>Solana position — liquidity deteriorating</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Position: <code>{html.escape(pid)}</code>",
            f"Mint: <code>{html.escape(_short(mint))}</code>",
            f"Quoted full-exit impact+slippage right now: <b>{combined_bps / Decimal(100):.2f}%</b> "
            f"(warning threshold: {warning_bps / Decimal(100):.2f}%)",
            "This is informational only — no exit was attempted and nothing was sold.",
            "If this keeps climbing, an exit may become unsafe under the automatic emergency "
            "ceiling later. Consider reviewing this position now while it may still be exitable "
            "within the ordinary execution guard, e.g. with /solanaforceexit if you decide to act.",
        ]),
    )


def check_open_position_liquidity(app) -> None:
    cfg = _sol.settings(app)
    interval = _sol._int(cfg.get("live_liquidity_health_check_seconds"), 900)
    warning_bps = max(Decimal(1), _d(cfg.get("live_liquidity_warning_combined_bps"), "150"))
    repeat_hours = _sol._int(cfg.get("live_liquidity_warning_repeat_hours"), 4)
    slippage_bps = _sol._int(cfg.get("live_order_slippage_bps"), 50)

    for position in _open_live_positions(app):
        pid = str(position.get("position_id") or "")
        mint = str(position.get("mint") or "")
        amount_raw = _sol._int(position.get("token_amount_raw"), 0)
        if not pid or not mint or amount_raw <= 0:
            continue
        if not _due_for_check(app, pid, interval):
            continue
        try:
            quote = _quote_only(mint, _sol.WSOL_MINT, amount_raw, slippage_bps)
            combined_bps = _combined_impact_slippage_bps(quote, slippage_bps)
        except Exception:
            # A failed/unavailable quote is not itself evidence of a liquidity
            # problem (could be a transient RPC/API issue); skip silently and try
            # again next cycle rather than alerting on noise.
            continue
        finally:
            _mark_checked(app, pid)
        if combined_bps >= warning_bps and _due_for_alert(app, pid, repeat_hours):
            _warn_position(app, position, combined_bps, warning_bps)
            _mark_alerted(app, pid)


_PREV_MONITOR_POSITIONS = _sol.monitor_positions


def monitor_positions_with_liquidity_health(app):
    result = _PREV_MONITOR_POSITIONS(app)
    try:
        check_open_position_liquidity(app)
    except Exception as exc:
        print("[solana-liquidity-health]", type(exc).__name__, exc)
    return result


def install():
    if getattr(_sol, "_position_liquidity_health_installed", False):
        return
    _sol.monitor_positions = monitor_positions_with_liquidity_health
    _sol._position_liquidity_health_installed = True
    print(
        "[solana-liquidity-health] check_interval=900s warning_combined_bps=150 "
        "repeat_alert_hours=4 action=notify_only never_executes_or_closes=true"
    )


install()
