from __future__ import annotations

"""Complete Cold Zone Telegram refusal coverage for the isolated learner.

The base Cold Zone strategy already queues most BUY refusals, but several early
reject paths only wrote cold_zone_decisions.  That made Telegram appear silent
while the strategy was actively rejecting opportunities.  This overlay queues a
bounded refusal notice for those missing paths without changing trading policy.
"""

from . import solana_cold_zone_strategy_patch as _cz

PROFILE = "COLD_ZONE_NOTICE_COMPLETENESS_V1"
_DEDUP_SECONDS = 60
_MISSING_NOTICE_CODES = {
    "POSITION_LIMIT",
    "POOL_TOO_OLD",
    "INSUFFICIENT_RESERVE",
    "SIGNING_OR_FUNDING",
}

_BASE_DECISION = _cz._decision
_LAST_SENT: dict[tuple[str, str, str], int] = {}


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
    _cz._cold_zone_notice_completeness_installed = True
    print(
        "[solana-cold-zone-notices] installed=true "
        f"profile={PROFILE} missing_reject_paths=telegram dedup={_DEDUP_SECONDS}s strategy_changed=false"
    )


install()
