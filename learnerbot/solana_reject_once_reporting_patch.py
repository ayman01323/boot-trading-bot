from __future__ import annotations

"""Persistent one-time Telegram reporting for identical Learner rejections.

SiLearn — 2026-08-29 12:23:54 BST — Subject: One-time rejection alerts + activate LP-unlocked LIVE revalidation

The same Telegram account + mint + leader + rejection reason is sent once only.
The transaction/signature is deliberately excluded from the identity, so repeated
leader transactions that hit the same condition do not spam Telegram.  The sent
marker is stored in the existing Solana state table, so a service restart does not
make the same condition alert again.  A different mint, leader, or reason remains
a different condition and is reported normally.

This module changes reporting only.  It does not alter BUY/SELL decisions,
PoolCheck, execution, signing, simulation, slippage, reserve, or kill switches.
"""

import hashlib
from contextlib import closing

from . import solana_leader_cursor_reliability_patch as _cursor
from . import solana_sibot as _sol

BOT_NAME = "SiLearn"
CHANGE_APPROVED_UTC = "2026-08-29T11:23:54Z"
CHANGE_APPROVED_BST = "2026-08-29T12:23:54+01:00"
CHANGE_SUBJECT = "One-time rejection alerts + activate LP-unlocked LIVE revalidation"
DEDUP_POLICY = "once_per_account_mint_leader_reason"
_STATE_PREFIX = "telegram_reject_once:v1:"


def _condition_identity(tid: str, event: dict, action: dict) -> str:
    mint = str(event.get("mint") or action.get("mint") or "").strip()
    leader = str(event.get("leader_wallet") or action.get("leader_wallet") or "").strip()
    reason = str(action.get("reason") or "unspecified rejection").strip()
    raw = "\x1f".join((str(tid), mint, leader, reason)).encode("utf-8", "replace")
    return _STATE_PREFIX + hashlib.sha256(raw).hexdigest()


def _already_sent(app, key: str) -> bool:
    with closing(_sol.connect(app)) as conn:
        return bool(_sol._state(conn, key, ""))


def _mark_sent(app, key: str) -> None:
    with closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, key, CHANGE_APPROVED_UTC)


def report_reject_actions_once(app, event: dict, actions: list[dict]) -> None:
    # Rejected-opportunity publication remains event-level research data; only the
    # Telegram operator presentation becomes persistent condition-level once-only.
    published: set[tuple[str, str]] = set()
    for raw in actions or []:
        action = dict(raw or {})
        if str(action.get("action") or "").upper() != "REJECT":
            continue

        reason = str(action.get("reason") or "unspecified rejection")
        mint = str(event.get("mint") or action.get("mint") or "")
        event_id = str(event.get("event_id") or event.get("signature") or action.get("signature") or "")
        klass = _cursor._reject_class(action)
        pub_key = (klass, reason)
        if pub_key not in published:
            _cursor.publish_rejection(
                chain="solana",
                token_address=mint,
                source="learnerbot",
                source_strategy_id=str(event.get("strategy_id") or "leader-copy"),
                source_event_id=event_id,
                rejection_class=klass,
                rejection_reason=reason,
                priority=75 if action.get("pool_risk_code") else 60,
                payload={
                    "risk_class": klass,
                    "leader_wallet": str(event.get("leader_wallet") or action.get("leader_wallet") or ""),
                    "source_runtime": "isolated_learner_solana",
                },
                require_market_reason=True,
            )
            published.add(pub_key)

        targets = _cursor._reject_targets(app, event, action)
        sent = 0
        suppressed = 0
        for tid in targets:
            key = _condition_identity(tid, event, action)
            if _already_sent(app, key):
                suppressed += 1
                continue
            try:
                if _cursor._send_reject_report(app, tid, event, action):
                    _mark_sent(app, key)
                    sent += 1
            except Exception as exc:
                # A failed Telegram send is NOT marked sent, so the condition can
                # retry later rather than being permanently lost.
                print("[learner-reject-once] telegram_error=%s" % str(exc)[:220])

        print(
            "[learner-reject-once] decision=REJECT targets=%d sent=%d suppressed=%d "
            "policy=%s class=%s reason=%s" % (
                len(targets), sent, suppressed, DEDUP_POLICY, klass, reason[:180]
            )
        )


def install() -> None:
    _cursor._report_reject_actions = report_reject_actions_once
    print(
        "[learner-reject-once] bot=SiLearn approved=2026-08-29T11:23:54Z "
        "bst=2026-08-29T12:23:54+01:00 policy=once_per_account_mint_leader_reason "
        "persistent=true signature_in_key=false reporting_only=true"
    )


install()
