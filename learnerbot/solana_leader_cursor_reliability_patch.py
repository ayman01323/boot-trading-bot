from __future__ import annotations

import time
from contextlib import closing

from . import solana_sibot as _sol


def _retryable_reason(text: str) -> bool:
    value = str(text or "").lower()
    return any(x in value for x in (
        "429", "rate limit", "too many requests", "timeout", "timed out",
        "temporarily unavailable", "service unavailable", "connection reset",
        "connection aborted", "gateway timeout", "jupiter quote failed",
    ))


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
        "transient_preflight_retry=true"
    )


install()
