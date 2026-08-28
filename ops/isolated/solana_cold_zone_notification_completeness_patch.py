from __future__ import annotations

"""Complete Cold Zone Telegram refusal coverage and suppress terminal old-pool repeats.

The base Cold Zone strategy queues most BUY refusals, but several early reject
paths only wrote cold_zone_decisions.  Earlier V1 added Telegram coverage with a
60-second in-memory dedup.  For POOL_TOO_OLD that was still noisy because a pool
older than 45 minutes can never become eligible for this Cold Zone strategy.

V2 therefore treats a recorded POOL_TOO_OLD mint as terminal for Cold Zone BUY
processing: after the first recorded rejection, later leader BUY events for the
same mint are skipped before PoolCheck/quote/profit work and do not create more
Telegram alerts or duplicate decisions.  The terminal state is read from SQLite,
so it survives service restarts.  Other refusal codes retain bounded 60-second
Telegram dedup and trading thresholds are unchanged.
"""

from contextlib import closing

from . import solana_cold_zone_strategy_patch as _cz

PROFILE = "COLD_ZONE_NOTICE_COMPLETENESS_V2_TERMINAL_MINT_DEDUP"
_DEDUP_SECONDS = 60
_MISSING_NOTICE_CODES = {
    "POSITION_LIMIT",
    "POOL_TOO_OLD",
    "INSUFFICIENT_RESERVE",
    "SIGNING_OR_FUNDING",
}

_BASE_DECISION = _cz._decision
_BASE_PROCESS = _cz._sol.process_leader_event
_LAST_SENT: dict[tuple[str, str, str], int] = {}


def _pool_too_old_already_recorded(app, mint: str) -> bool:
    """True once this mint has permanently aged out of the 0-45m Cold Zone."""
    mint = str(mint or "").strip()
    if not mint:
        return False
    try:
        with closing(_cz._sol.connect(app)) as conn:
            row = conn.execute(
                "SELECT 1 FROM cold_zone_decisions "
                "WHERE mint=? AND reason_code='POOL_TOO_OLD' LIMIT 1",
                (mint,),
            ).fetchone()
        return row is not None
    except Exception:
        # Never suppress processing merely because the dedup lookup failed.
        return False


def process_with_terminal_old_pool_dedup(app, event: dict):
    action = str((event or {}).get("action") or "").upper()
    mint = str((event or {}).get("mint") or "").strip()
    if action == "BUY" and mint and _pool_too_old_already_recorded(app, mint):
        return [
            {
                "action": "SKIP",
                "reason": "COLD_ZONE terminal old-pool mint already rejected",
                "mint": mint,
            }
        ]
    return _BASE_PROCESS(app, event)


def decision_with_complete_notices(app, tid: str, event: dict, decision: str, code: str, reason: str, details: dict | None = None) -> None:
    _BASE_DECISION(app, tid, event, decision, code, reason, details)
    code = str(code or "")
    if code not in _MISSING_NOTICE_CODES:
        return

    mint = str((event or {}).get("mint") or "")
    key = (str(tid), mint, code)
    now = _cz._now()
    if now - int(_LAST_SENT.get(key) or 0) < _DEDUP_SECONDS:
        return

    payload = details or {}
    pool_age = None
    try:
        if payload.get("pool_age_seconds") is not None:
            pool_age = max(0, int(payload.get("pool_age_seconds")))
    except Exception:
        pool_age = None

    warnings = []
    try:
        raw = payload.get("warnings") or []
        if isinstance(raw, (list, tuple, set)):
            warnings = [str(x) for x in raw if str(x)]
    except Exception:
        warnings = []

    _cz._queue_notice(
        app,
        str(tid),
        "BUY_REFUSED",
        _cz._entry_rejection_message(pool_age, str(reason), warnings=warnings),
        mint=mint,
    )
    _LAST_SENT[key] = now


def install() -> None:
    if getattr(_cz, "_cold_zone_notice_completeness_installed", False):
        return
    _cz._decision = decision_with_complete_notices
    _cz._sol.process_leader_event = process_with_terminal_old_pool_dedup
    _cz._cold_zone_notice_completeness_installed = True
    print(
        "[solana-cold-zone-notices] installed=true "
        f"profile={PROFILE} missing_reject_paths=telegram dedup={_DEDUP_SECONDS}s "
        "pool_too_old=terminal_mint_persistent_skip strategy_changed=false"
    )


install()
