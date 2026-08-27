from __future__ import annotations

from .poolcheck_bridge import MandatoryShadowPoolCheck
from .rejected_reporting import publish_intent_rejection

_ORIGINAL = MandatoryShadowPoolCheck.assess_entry
_INSTALLED = False


def _assess_entry(self, intent):
    decision = _ORIGINAL(self, intent)
    verdict = str(getattr(decision, "verdict", "") or "").upper()
    if verdict not in {"PASS", "SHADOW_ONLY"}:
        reasons = list(getattr(decision, "reasons", ()) or ())
        reason = " | ".join(str(x) for x in reasons if str(x)) or f"PoolCheck {verdict or 'REJECT'}"
        publish_intent_rejection(
            intent,
            f"POOLCHECK_{verdict or 'REJECT'}",
            reason,
            payload={"poolcheck_verdict": verdict, "poolcheck_reasons": reasons},
        )
    return decision


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    MandatoryShadowPoolCheck.assess_entry = _assess_entry
    _INSTALLED = True


install()
