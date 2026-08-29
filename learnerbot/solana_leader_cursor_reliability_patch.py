from __future__ import annotations

import html
import time
from contextlib import closing

from . import solana_sibot as _sol
from .rejected_opportunity_publisher import publish_rejection
from .solana_live_patch import live_enabled
from .telegram import send_message
from .user_registry import all_users


# Change Set 5
# Approved: 2026-08-29T11:05:51Z / 2026-08-29 12:05:51 BST
# Subject: Learner Telegram rejection reporting only.
# Same account + mint + leader + rejection reason is sent at most once per 15 min.
# Full mint/leader/signature values are visible and clickable in Telegram.
# Trading decisions, PoolCheck and execution behaviour are not changed here.
_REJECT_REPORT_SUPPRESS_SECONDS = 15 * 60
_REJECT_REPORT_DEDUP: dict[tuple[str, str, str, str], float] = {}


def _retryable_reason(text: str) -> bool:
    value = str(text or "").lower()
    return any(x in value for x in (
        "429", "rate limit", "too many requests", "timeout", "timed out",
        "temporarily unavailable", "service unavailable", "connection reset",
        "connection aborted", "gateway timeout", "jupiter quote failed",
    ))


def _reject_class(action: dict) -> str:
    explicit = str(action.get("pool_risk_code") or action.get("rejection_class") or "").strip()
    if explicit:
        return explicit[:80]
    reason = str(action.get("reason") or "").strip()
    head = reason.split(":", 1)[0].strip().upper().replace(" ", "_").replace("-", "_")
    return (head or "LEARNER_REJECT")[:80]


def _reject_targets(app, event: dict, action: dict) -> list[str]:
    tid = str(action.get("telegram_id") or "").strip()
    if tid:
        return [tid]
    wallet = str(event.get("leader_wallet") or "")
    out: list[str] = []
    try:
        for user in all_users(app.csv_dir, enabled_only=True):
            candidate = str(user.get("telegram_id") or "").strip()
            if not candidate or not live_enabled(app, candidate):
                continue
            if not _sol._sibot._bool(_sol._sibot.user_settings(app, candidate, 0).get("enabled"), False):
                continue
            if wallet and _sol._leader_rank(app, candidate, wallet) is None:
                continue
            out.append(candidate)
    except Exception as exc:
        print("[learner-reject-report] target_error=%s:%s" % (type(exc).__name__, str(exc)[:160]))
    return list(dict.fromkeys(out))


def _telegram_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    description = ""
    if response is not None:
        try:
            data = response.json()
            description = str(data.get("description") or "")[:180]
        except Exception:
            description = ""
    if description:
        return f"status={status or 'unknown'} description={description}"
    return f"status={status or 'unknown'} type={type(exc).__name__}"


def _telegram_link(url: str, label: str) -> str:
    return '<a href="%s">%s</a>' % (
        html.escape(str(url or ""), quote=True),
        html.escape(str(label or "")),
    )


def _reject_message(event: dict, action: dict, *, test_only: bool = False) -> str:
    reason = str(action.get("reason") or "unspecified rejection")
    mint = str(event.get("mint") or action.get("mint") or "unknown")
    wallet = str(event.get("leader_wallet") or action.get("leader_wallet") or "")
    signature = str(event.get("signature") or event.get("event_id") or action.get("signature") or "")

    mint_link = _telegram_link(f"https://solscan.io/token/{mint}", f"solana:{mint}")
    dexview_link = _telegram_link(f"https://www.dexview.com/solana/{mint}", "Open Dexview")
    lines = [
        "⛔ <b>LEARNER REJECTED OPPORTUNITY</b>" if not test_only else "🧪 <b>LEARNER REJECT REPORT TEST — PASS PATH</b>",
        f"Asset: {mint_link}",
        f"Reason: <code>{html.escape(reason[:700])}</code>",
        f"Dexview: {dexview_link}",
    ]
    if wallet:
        lines.append(
            "Leader: " + _telegram_link(f"https://solscan.io/account/{wallet}", wallet)
        )
    if signature:
        lines.append(
            "Signal: " + _telegram_link(f"https://solscan.io/tx/{signature}", signature)
        )
    lines.append("Decision: <b>NO BUY / NO BROADCAST</b>")
    if test_only:
        lines.append("Synthetic reporting test only — no market opportunity was evaluated and no trade was placed.")
    return "\n".join(lines)


def _send_reject_report(app, tid: str, event: dict, action: dict, *, test_only: bool = False) -> bool:
    if not getattr(app, "telegram_bot_token", ""):
        return False
    try:
        sent_count = send_message(
            app.telegram_bot_token,
            str(tid),
            _reject_message(event, action, test_only=test_only),
            parse_mode="HTML",
            protect_content=True,
        )
    except Exception as exc:
        raise RuntimeError("telegram_send_failed " + _telegram_error_text(exc)) from None
    return int(sent_count or 0) > 0


def _telegram_reject_dedup_key(tid: str, event: dict, action: dict) -> tuple[str, str, str, str]:
    """Deduplicate the *condition*, not the signal transaction.

    The former key included the transaction signature. A leader could therefore
    trigger the same rejected mint/reason dozens of times and every signature
    generated a new Telegram alert. The signal ID is intentionally excluded here.
    """
    mint = str(event.get("mint") or action.get("mint") or "").strip()
    wallet = str(event.get("leader_wallet") or action.get("leader_wallet") or "").strip()
    reason = str(action.get("reason") or "unspecified rejection").strip()
    return str(tid), mint, wallet, reason


def _report_reject_actions(app, event: dict, actions: list[dict]) -> None:
    now = time.time()
    for key, ts in list(_REJECT_REPORT_DEDUP.items()):
        if now - ts > _REJECT_REPORT_SUPPRESS_SECONDS:
            _REJECT_REPORT_DEDUP.pop(key, None)

    # Queue publication remains event-level research data. Telegram presentation is
    # condition-level deduplicated so the operator is not spammed by every signature.
    published: set[tuple[str, str]] = set()
    for raw in actions or []:
        action = dict(raw or {})
        if str(action.get("action") or "").upper() != "REJECT":
            continue
        reason = str(action.get("reason") or "unspecified rejection")
        mint = str(event.get("mint") or action.get("mint") or "")
        event_id = str(event.get("event_id") or event.get("signature") or action.get("signature") or "")
        klass = _reject_class(action)
        pub_key = (klass, reason)
        if pub_key not in published:
            publish_rejection(
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

        targets = _reject_targets(app, event, action)
        sent = 0
        suppressed = 0
        for tid in targets:
            dedup_key = _telegram_reject_dedup_key(tid, event, action)
            last = _REJECT_REPORT_DEDUP.get(dedup_key)
            if last is not None and now - last < _REJECT_REPORT_SUPPRESS_SECONDS:
                suppressed += 1
                continue
            try:
                if _send_reject_report(app, tid, event, action):
                    sent += 1
                    _REJECT_REPORT_DEDUP[dedup_key] = now
            except Exception as exc:
                print("[learner-reject-report] telegram_error=%s" % str(exc)[:220])
        print(
            "[learner-reject-report] decision=REJECT targets=%d sent=%d suppressed=%d "
            "dedup_seconds=%d class=%s reason=%s" % (
                len(targets), sent, suppressed, _REJECT_REPORT_SUPPRESS_SECONDS,
                klass, reason[:180],
            )
        )


def _leader_signatures(app, wallet: str, limit=50):
    """Low-latency selected-leader feed.

    Research/history remains finalized in solana_sibot. Copy execution is allowed
    to observe confirmed transactions so the 30-second freshness gate is not
    consumed waiting for finalization.
    """
    opts = {
        "commitment": "confirmed",
        "limit": max(1, min(100, int(limit))),
    }
    return _sol._rpc(app, "getSignaturesForAddress", [str(wallet), opts]) or []


def _leader_transaction(app, signature: str):
    return _sol._rpc(app, "getTransaction", [
        str(signature),
        {
            "commitment": "confirmed",
            "maxSupportedTransactionVersion": 0,
            "encoding": "jsonParsed",
        },
    ])


def _has_open_position_for_leader(app, wallet: str) -> bool:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT 1 FROM positions WHERE leader_wallet=? AND status='OPEN' LIMIT 1",
            (str(wallet),),
        ).fetchone()
    return bool(row)


def _can_fast_forward_stale_row(app, wallet: str, row: dict, cfg: dict) -> bool:
    """Skip an already-untradeable old signature only when no exit can depend on it."""
    try:
        block_time = int(row.get("blockTime") or 0)
    except Exception:
        block_time = 0
    if block_time <= 0:
        return False
    max_age = max(1, _sol._int(cfg.get("max_signal_age_seconds"), 30))
    # A small grace window prevents a borderline signal being discarded before
    # normal classification. Once older than this it cannot pass the entry gate.
    if int(time.time()) - block_time <= max_age + 5:
        return False
    return not _has_open_position_for_leader(app, wallet)


def monitor_leaders_reliable(app):
    cfg = _sol.settings(app)
    with closing(_sol.connect(app)) as conn:
        leaders = [str(r["wallet"]) for r in conn.execute(
            "SELECT DISTINCT wallet FROM leaders"
        ).fetchall()]
    events = []
    for wallet in leaders:
        try:
            rows = _leader_signatures(app, wallet, 50)
        except Exception:
            continue
        if not rows:
            continue
        with closing(_sol.connect(app)) as conn:
            key = f"leader_last_signature:{wallet}"
            last = _sol._state(conn, key, "") or ""
            if not last:
                # Start at the newest confirmed signature. Never copy historical
                # activity merely because the service was just restarted.
                _sol._set_state(conn, key, str(rows[0].get("signature") or ""))
                continue

        new = []
        for row in rows:
            sig = str(row.get("signature") or "")
            if sig == last:
                break
            new.append(row)

        processed_signature = last
        for row in reversed(new):
            sig = str(row.get("signature") or "")
            if not sig:
                continue
            if row.get("err") is not None:
                processed_signature = sig
                continue

            # If this signature is already too old ever to become a fresh BUY and
            # this leader has no open copied position that could need an old SELL,
            # advance without a costly getTransaction call. This prevents a stale
            # backlog from delaying the next genuinely fresh signal.
            if _can_fast_forward_stale_row(app, wallet, row, cfg):
                processed_signature = sig
                continue

            try:
                tx = _leader_transaction(app, sig)
                if not tx:
                    break
                event = _sol.classify_swap(tx, wallet)
                if event:
                    saved = _sol._record_leader_event(app, wallet, event)
                    # Re-processing an already stored leader event is safe: LIVE
                    # chain execution has its own durable attempt key. It is needed
                    # when the previous pass failed during preflight before claiming.
                    payload = saved or {
                        **event,
                        "leader_wallet": wallet,
                    }
                    if saved:
                        events.append(saved)
                    actions = _sol.process_leader_event(app, payload) or []
                    _report_reject_actions(app, payload, actions)
                    if any(
                        str(a.get("action") or "").upper() == "REJECT"
                        and _retryable_reason(a.get("reason"))
                        for a in actions
                    ):
                        break
                processed_signature = sig
            except Exception as exc:
                print(
                    "[sibot-solana-leader-signature]",
                    wallet[:10], sig[:10], type(exc).__name__, str(exc)[:180],
                )
                break

        if processed_signature != last:
            with closing(_sol.connect(app)) as conn:
                _sol._set_state(conn, f"leader_last_signature:{wallet}", processed_signature)
    return events


def install():
    _sol.monitor_leaders = monitor_leaders_reliable
    print(
        "[solana-leader-cursor] confirmed_fast_lane=true "
        "processed_signature_checkpoint=true stale_backlog_fast_forward=true "
        "transient_preflight_retry=true reject_telegram_dedup_seconds=900 "
        "reject_full_clickable_ids=true changeset=5 approved=2026-08-29T11:05:51Z"
    )


install()
