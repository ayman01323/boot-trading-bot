from __future__ import annotations

import time
from .csvio import append_row

LEGACY_HEADERS = [
    "review_epoch", "position_id", "strategy_id", "age_class", "temperature",
    "net_pct", "exit_reason", "recommendation", "apply_status",
]

HRCWH_HEADERS = [
    "review_epoch", "position_id", "strategy_id", "age_class", "entry_temperature",
    "net_pct", "exit_reason", "hold_seconds", "catastrophic_loss", "profitable",
    "profit_band_2_5", "recommendation", "apply_status",
]


class Stage8Review:
    """Evidence-only review stage. Never changes LIVE settings itself."""

    def __init__(self, settings):
        self.settings = settings

    def review(self, closed_row, context=None):
        context = context or {}
        net = float(closed_row.get("net_pct") or 0)
        reason = str(closed_row.get("exit_reason") or "")
        now = int(time.time())
        try:
            opened = int(float(context.get("opened_epoch") or now))
        except Exception:
            opened = now
        hold_seconds = max(0, now - opened)
        catastrophic = net <= -15.0 or "RUG" in reason.upper()
        profitable = net > 0
        profit_band = 2.0 <= net <= 5.0

        recommendation = "KEEP_PARAMETERS"
        if catastrophic:
            recommendation = "HARDEN_RISK_AND_RUG_GATES"
        elif reason in {"FAST_STOP", "HOT_REVERSAL", "EXIT_HEALTH"} and net < 0:
            recommendation = "REVIEW_ENTRY_AND_EXIT_HEALTH"
        elif reason == "MAX_HOLD_TIME" and net <= 0:
            recommendation = "REVIEW_ENTRY_OR_SHORTEN_HOLD"
        elif reason in {"FAST_TAKE_PROFIT", "WARM_REVERSAL"} and profitable:
            recommendation = "KEEP_AND_COLLECT_MORE_SAMPLES"
        elif profitable:
            recommendation = "KEEP_AND_COLLECT_MORE_SAMPLES"

        legacy = {
            "review_epoch": now,
            "position_id": closed_row.get("position_id", ""),
            "strategy_id": str(context.get("strategy_id") or ""),
            "age_class": str(context.get("age_class") or ""),
            "temperature": str(context.get("temperature") or ""),
            "net_pct": net,
            "exit_reason": reason,
            "recommendation": recommendation,
            "apply_status": "PROPOSED_ONLY",
        }
        append_row(self.settings.csv_dir / "stage8_reviews.csv", LEGACY_HEADERS, legacy)

        extended = {
            "review_epoch": now,
            "position_id": closed_row.get("position_id", ""),
            "strategy_id": str(context.get("strategy_id") or ""),
            "age_class": str(context.get("age_class") or ""),
            "entry_temperature": str(context.get("temperature") or ""),
            "net_pct": net,
            "exit_reason": reason,
            "hold_seconds": hold_seconds,
            "catastrophic_loss": str(catastrophic).lower(),
            "profitable": str(profitable).lower(),
            "profit_band_2_5": str(profit_band).lower(),
            "recommendation": recommendation,
            "apply_status": "PROPOSED_ONLY",
        }
        append_row(self.settings.csv_dir / "stage8_hrcwh_reviews.csv", HRCWH_HEADERS, extended)
        return extended
