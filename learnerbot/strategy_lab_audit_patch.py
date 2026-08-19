from __future__ import annotations

# Reporting-only composition patch.  It does not execute trades or alter trading hooks.
# It enriches the existing sanitised hourly loss-forensics payload with a separate
# scorecard for every Strategy Laboratory hypothesis so the independent AI reviewers
# can compare, improve, rework or replace strategies without conflating them.

from . import loss_forensics_github_export as _forensics
from .strategy_lab import portfolio_report, seed_creative_hypotheses

_PREV_BUILD = _forensics.build_loss_forensics


def build_loss_forensics_with_strategy_lab(app, zip_path, gpt_result=None, *, hours=_forensics.WINDOW_HOURS):
    report = _PREV_BUILD(app, zip_path, gpt_result, hours=hours)
    try:
        seeded = seed_creative_hypotheses(app)
        lab = portfolio_report(app)
        lab["new_hypotheses_seeded_this_run"] = [
            {
                "strategy_id": row.get("strategy_id"),
                "name": row.get("name"),
                "family": row.get("family"),
                "status": row.get("status"),
            }
            for row in seeded
        ]
        lab["ai_review_instruction"] = (
            "Review every strategy separately. Suggest creative new asset-neutral strategy hypotheses as SHADOW only. "
            "Do not reward raw trade count. Reward money-weighted net profit after recorded costs, opportunity participation, "
            "execution quality and robustness. An ACTIVE strategy should pursue eligible positive-edge opportunities; if it "
            "repeatedly finds eligible opportunities but does not participate, recommend bounded rework. If it is adequately "
            "sampled and net-negative, recommend rework or replacement. Never force a trade merely to satisfy activity."
        )
        report["strategy_lab"] = lab
    except Exception as exc:
        report["strategy_lab"] = {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "live_auto_promote": False,
        }
    return report


_forensics.build_loss_forensics = build_loss_forensics_with_strategy_lab
