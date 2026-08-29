from __future__ import annotations

"""Register the owner-approved second MASTER and route operator alerts to MASTERs.

SiLearn — 2026-08-29 17:26 BST — Subject: Register MASTER 6760898817 and include MASTERs in rejection alerts

Reporting/account-role change only. This module does not enable LIVE, AUTO, SiBot,
learner entries, signing, or any trade setting for the target account. It ensures
Telegram 6760898817 exists as an ACTIVE MASTER in the isolated learner registry and
adds ACTIVE MASTER accounts to rejection and LP-reference-warning recipients.
"""

import html
import time

from . import cli as _cli
from . import solana_leader_cursor_reliability_patch as _cursor
from . import solana_lp_warning_only_patch as _lp
from .user_registry import all_users, get_user, join_user, update_user

BOT_NAME = "SiLearn"
TARGET_TELEGRAM_ID = "6760898817"
CHANGE_APPROVED_UTC = "2026-08-29T16:26:00Z"
CHANGE_APPROVED_BST = "2026-08-29T17:26:00+01:00"
CHANGE_SUBJECT = "Register MASTER 6760898817 and include MASTERs in rejection alerts"

_PREV_APP = _cli._app
_PREV_REJECT_TARGETS = _cursor._reject_targets
_PREV_LP_WARNING_SEND = _lp._send_reference_warning


def _ensure_target_master(app) -> None:
    row = get_user(app.csv_dir, TARGET_TELEGRAM_ID)
    if row is None:
        # join_user creates only a neutral registry row; no LIVE/AUTO setting is
        # written here. We then promote the explicitly authorised ID to MASTER.
        join_user(app.csv_dir, TARGET_TELEGRAM_ID, "MASTER")
    update_user(
        app.csv_dir,
        TARGET_TELEGRAM_ID,
        role="MASTER",
        status="ACTIVE",
        fee_plan_id="MASTER",
        label="Master 6760898817",
        allowed_chains="*",
        max_wallets="20",
        activated_epoch=int(time.time()),
        notes="SiLearn 2026-08-29 17:26 BST: owner-approved MASTER registration for Telegram reporting; trading settings unchanged",
    )


def _active_master_ids(app) -> list[str]:
    out: list[str] = []
    for row in all_users(app.csv_dir, enabled_only=True):
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        if str(row.get("role") or "").upper() != "MASTER":
            continue
        tid = str(row.get("telegram_id") or "").strip()
        if tid:
            out.append(tid)
    return list(dict.fromkeys(out))


def _reject_targets_with_masters(app, event: dict, action: dict) -> list[str]:
    targets = list(_PREV_REJECT_TARGETS(app, event, action) or [])
    targets.extend(_active_master_ids(app))
    return list(dict.fromkeys(str(tid) for tid in targets if str(tid).strip()))


def _send_lp_reference_warning_with_masters(app, event: dict, cfg: dict, warnings: list[str]) -> None:
    # Preserve the existing trading-user warning recipients first.
    _PREV_LP_WARNING_SEND(app, event, cfg, warnings)

    mint = str(event.get("mint") or "").strip()
    leader = str(event.get("leader_wallet") or "").strip()
    signal = str(event.get("signature") or event.get("event_id") or "").strip()
    warning_lines = "\n".join(f"• {html.escape(str(item))}" for item in warnings[:8])
    asset_html = html.escape(mint)
    leader_html = html.escape(leader)
    signal_html = html.escape(signal)

    for tid in _active_master_ids(app):
        key = _lp._warning_key(tid, mint, warnings)
        if _lp._already_warned(app, key):
            continue
        links = [f'Asset: <a href="https://solscan.io/token/{asset_html}">{asset_html}</a>']
        if leader:
            links.append(f'Leader: <a href="https://solscan.io/account/{leader_html}">{leader_html}</a>')
        if signal:
            links.append(f'Signal: <a href="https://solscan.io/tx/{signal_html}">{signal_html}</a>')
        message = (
            "⚠️ <b>SiLearn LP reference warning</b>\n"
            + "\n".join(links)
            + "\n\n"
            + warning_lines
            + "\n\n<b>Decision impact: WARNING ONLY</b>\n"
              "These LP lock/provider signals do not block the BUY. Pool depth, age, activity, "
              "price consistency, liquidity-collapse, reverse-sell depth and all other safety/execution gates still decide."
        )
        try:
            _lp._live._notify(app, str(tid), message)
            _lp._mark_warned(app, key)
        except Exception as exc:
            print("[silearn-master-alerts] telegram_lp_warning_error=%s" % str(exc)[:220])


def _app_with_master_676():
    app = _PREV_APP()
    try:
        _ensure_target_master(app)
    except Exception as exc:
        print("[silearn-master-alerts] registration_error=%s:%s" % (type(exc).__name__, str(exc)[:220]))
    return app


def install() -> None:
    _cli._app = _app_with_master_676
    _cursor._reject_targets = _reject_targets_with_masters
    _lp._send_reference_warning = _send_lp_reference_warning_with_masters
    print(
        "[silearn-master-alerts] bot=SiLearn approved=2026-08-29T16:26:00Z "
        "bst=2026-08-29T17:26:00+01:00 target=6760898817 role=MASTER "
        "reject_alerts=ALL_ACTIVE_MASTERS lp_reference_alerts=ALL_ACTIVE_MASTERS "
        "trading_settings_unchanged=true"
    )


install()
