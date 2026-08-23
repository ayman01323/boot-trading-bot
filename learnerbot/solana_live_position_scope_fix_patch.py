from __future__ import annotations

from contextlib import closing

from . import solana_sibot as _sol
from . import telegram_ui as _ui


# Final runtime correction for two distinct issues that made a healthy LIVE
# system look/behave blocked:
# 1) solana_live_patch uses _sol._open_position() as the same-mint duplicate
#    guard, but the base helper matched SHADOW rows too. A SHADOW row must never
#    consume or block a LIVE position slot.
# 2) the global Solana status counter mixed SHADOW and LIVE open positions, so
#    the operator could see "open positions 2" even when LIVE capacity was free.
#
# This patch does NOT raise live_max_positions, relax entry quality, bypass
# simulation/reserve/liquidity checks, or change signing/LIVE gates.

_PREV_STATUS = _sol.status
_PREV_STATUS_PAGE = _ui.status_page


def _open_live_position(app, tid, mint):
    """Return only an OPEN LIVE position for the user+mint duplicate guard."""
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            """SELECT * FROM positions
               WHERE telegram_id=? AND mint=? AND status='OPEN' AND mode='LIVE'
               ORDER BY entry_ts LIMIT 1""",
            (str(tid), str(mint)),
        ).fetchone()
        return dict(row) if row else None


def status(app):
    """Keep legacy keys but make open_positions mean real LIVE capacity usage."""
    out = dict(_PREV_STATUS(app))
    with closing(_sol.connect(app)) as conn:
        live_open = int(conn.execute(
            "SELECT COUNT(*) n FROM positions WHERE status='OPEN' AND mode='LIVE'"
        ).fetchone()["n"])
        shadow_open = int(conn.execute(
            "SELECT COUNT(*) n FROM positions WHERE status='OPEN' AND mode='SHADOW'"
        ).fetchone()["n"])
        reconcile_required = int(conn.execute(
            "SELECT COUNT(*) n FROM positions WHERE status='RECONCILE_REQUIRED' AND mode='LIVE'"
        ).fetchone()["n"])
    out["open_positions"] = live_open
    out["open_live_positions"] = live_open
    out["open_shadow_positions"] = shadow_open
    out["reconcile_required_positions"] = reconcile_required
    return out


def status_page(*args, **kwargs):
    text = _PREV_STATUS_PAGE(*args, **kwargs)
    # Presentation-only clarification. Preserve all existing formatting/wrappers.
    return str(text).replace("| open positions ", "| open LIVE positions ")


def install():
    # solana_live_patch resolves _sol._open_position dynamically at decision time,
    # so this narrows only its duplicate-position lookup without replacing the
    # audited execution function or any of its safety gates.
    _sol._open_position = _open_live_position
    _sol.status = status
    _ui.status_page = status_page
    print(
        "[solana-live-position-scope-fix] duplicate_guard=LIVE_only "
        "global_open_positions=LIVE_only risk_cap=unchanged"
    )


install()
