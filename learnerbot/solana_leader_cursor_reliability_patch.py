from __future__ import annotations

from contextlib import closing

from . import solana_sibot as _sol


def _retryable_reason(text: str) -> bool:
    value = str(text or "").lower()
    return any(x in value for x in (
        "429", "rate limit", "too many requests", "timeout", "timed out",
        "temporarily unavailable", "service unavailable", "connection reset",
        "connection aborted", "gateway timeout", "jupiter quote failed",
    ))


def monitor_leaders_reliable(app):
    with closing(_sol.connect(app)) as conn:
        leaders = [str(r["wallet"]) for r in conn.execute(
            "SELECT DISTINCT wallet FROM leaders"
        ).fetchall()]
    events = []
    for wallet in leaders:
        try:
            rows = _sol._get_signatures(app, wallet, 50)
        except Exception:
            continue
        if not rows:
            continue
        with closing(_sol.connect(app)) as conn:
            key = f"leader_last_signature:{wallet}"
            last = _sol._state(conn, key, "") or ""
            if not last:
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
            try:
                tx = _sol._get_transaction(app, sig)
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
                print("[sibot-solana-leader-signature]", wallet[:10], sig[:10], type(exc).__name__, str(exc)[:180])
                break

        if processed_signature != last:
            with closing(_sol.connect(app)) as conn:
                _sol._set_state(conn, f"leader_last_signature:{wallet}", processed_signature)
    return events


def install():
    _sol.monitor_leaders = monitor_leaders_reliable
    print("[solana-leader-cursor] processed_signature_checkpoint=true transient_preflight_retry=true")


install()
