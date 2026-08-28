from __future__ import annotations

from .poolcheck_bridge import MandatoryShadowPoolCheck
from .rejected_reporting import publish_intent_rejection

_ORIGINAL = MandatoryShadowPoolCheck.assess_entry
_INSTALLED = False


def _assess_entry(self, intent):
    decision = _ORIGINAL(self, intent)
    verdict = str(getattr(decision, "verdict", "") or "").upper()
    reasons = list(getattr(decision, "reasons", ()) or ())
    reason = " | ".join(str(x) for x in reasons if str(x)) or f"PoolCheck {verdict or 'REJECT'}"
    metadata = dict(getattr(intent, "metadata", {}) or {})
    payload = {
        **metadata,
        "poolcheck_verdict": verdict,
        "poolcheck_reasons": reasons,
    }

    if verdict == "SHADOW_ONLY":
        # SHADOW_ONLY is not inserted into the SiRisky queue here: it is a refusal
        # of LIVE execution, but changing queue eligibility would change trading
        # behaviour. The operator still needs to see it immediately in Telegram.
        try:
            from learnerbot.rejected_opportunity_publisher import notify_rejection_only

            notify_rejection_only(
                candidate_id=str(getattr(intent, "intent_id", "") or ""),
                chain=str(getattr(intent, "chain", "") or ""),
                token_address=str(getattr(intent, "asset_out", "") or ""),
                pool_address=str(metadata.get("pool_id") or metadata.get("pool_address") or ""),
                dex=str(getattr(intent, "venue", "") or metadata.get("venue") or ""),
                source=str(getattr(intent, "engine_id", "") or "sibot1"),
                source_strategy_id=str(getattr(intent, "strategy_id", "") or ""),
                source_event_id=str(
                    getattr(intent, "market_event_id", "")
                    or getattr(intent, "intent_id", "")
                    or ""
                ),
                rejection_class="POOLCHECK_SHADOW_ONLY",
                rejection_reason=reason,
                payload=payload,
            )
        except Exception:
            pass
    elif verdict != "PASS":
        publish_intent_rejection(
            intent,
            f"POOLCHECK_{verdict or 'REJECT'}",
            reason,
            payload=payload,
        )
    return decision


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    MandatoryShadowPoolCheck.assess_entry = _assess_entry
    _INSTALLED = True


install()
