from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_QUEUE_ROOT = Path(
    os.environ.get(
        "BOOT_REJECTED_OPPORTUNITY_DIR",
        "/home/ayman01323/BOOT/data/candidates/REJECTED OPPORTUNITY",
    )
)
DEFAULT_CODE_ROOT = Path(
    os.environ.get(
        "BOOT_REJECTED_OPPORTUNITY_CODE_ROOT",
        "/home/ayman01323/BOOT/datacentre/rejected_router",
    )
)

MARKET_TERMS = (
    "POOL", "LIQUID", "LP_", "LP ", "RUGCHECK", "REVERSE", "SELLABILITY",
    "PRICE IMPACT", "ROUNDTRIP", "ROUND-TRIP", "DEX", "HONEYPOT", "FREEZE",
    "MINT AUTHORITY", "DEVELOPER", "DEV_", "SIMULATION", "QUOTE", "SLIPPAGE",
    "ROUTE", "TOKEN_SECURITY", "COOLING", "WASH_VOLUME", "CROSS_POOL",
)
NON_MARKET_TERMS = (
    "ACCOUNT AUTOMATIC", "SIGNER", "NOT FUNDED", "INSUFFICIENT BALANCE",
    "INSUFFICIENT SOL", "PERMISSION IS OFF", "LIVE IS OFF", "AUTO IS OFF",
    "ARMED IS OFF", "NOT ARMED", "KILL-SWITCH", "CAPITAL BASIS", "DRAWDOWN",
)


def _enabled() -> bool:
    return str(os.environ.get("BOOT_REJECTED_OPPORTUNITY_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes", "on", "y"
    }


def market_rejection(reason: str) -> bool:
    text = str(reason or "").upper()
    if not text or any(term in text for term in NON_MARKET_TERMS):
        return False
    return any(term in text for term in MARKET_TERMS)


def source_bot(default: str = "learnerbot") -> str:
    explicit = str(os.environ.get("BOOT_REJECTED_SOURCE_BOT", "")).strip().lower()
    if explicit:
        return explicit
    if str(os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "")).strip():
        return "claude"
    return str(default or "learnerbot").strip().lower()


def _queue():
    if str(DEFAULT_CODE_ROOT) not in sys.path:
        sys.path.insert(0, str(DEFAULT_CODE_ROOT))
    from boot_platform.rejected_opportunity_queue import RejectedOpportunityQueue
    return RejectedOpportunityQueue(DEFAULT_QUEUE_ROOT)


def publish_rejection(
    *,
    chain: str,
    token_address: str,
    rejection_class: str,
    rejection_reason: str,
    source: str = "learnerbot",
    pool_address: str = "",
    dex: str = "",
    source_strategy_id: str = "",
    source_event_id: str = "",
    priority: int = 60,
    payload: dict[str, Any] | None = None,
    observed_at: int | None = None,
    require_market_reason: bool = True,
):
    """Publish one rejection without ever blocking the trading process.

    Queue/file and Telegram failures are swallowed. Refusal reporting is an
    observability side effect only and never changes a trading decision.
    """
    if not _enabled():
        return None
    token = str(token_address or "").strip()
    reason = str(rejection_reason or "").strip()
    if not token or (require_market_reason and not market_rejection(reason)):
        return None
    try:
        resolved_source = source_bot(source)
        resolved_payload = dict(payload or {})
        result = _queue().publish(
            chain=str(chain or "").strip().lower(),
            token_address=token,
            source_bot=resolved_source,
            rejection_reason=reason,
            pool_address=str(pool_address or ""),
            dex=str(dex or ""),
            source_strategy_id=str(source_strategy_id or ""),
            source_event_id=str(source_event_id or ""),
            rejection_class=str(rejection_class or "MARKET_RISK_REJECT"),
            priority=int(priority),
            observed_at=int(observed_at or time.time()),
            payload=resolved_payload,
        )
        print(
            "[rejected-opportunity] published=true source=%s chain=%s class=%s candidate=%s status=%s" % (
                resolved_source, str(chain or "").lower(), str(rejection_class or ""),
                result.candidate_id, result.status,
            ),
            flush=True,
        )
        try:
            from learnerbot.rejected_opportunity_telegram import notify_rejected_opportunity_async

            notify_rejected_opportunity_async(
                candidate_id=result.candidate_id,
                inserted_observation=bool(result.inserted_observation),
                chain=str(chain or "").strip().lower(),
                token_address=token,
                pool_address=str(pool_address or ""),
                dex=str(dex or ""),
                source_bot=resolved_source,
                source_strategy_id=str(source_strategy_id or ""),
                rejection_class=str(rejection_class or "MARKET_RISK_REJECT"),
                rejection_reason=reason,
                payload=resolved_payload,
            )
        except Exception:
            pass
        return result
    except Exception as exc:  # data publication must never break trading safety logic
        print(
            "[rejected-opportunity] published=false error=%s source=%s" % (
                type(exc).__name__, source_bot(source)
            ),
            file=sys.stderr,
            flush=True,
        )
        return None
