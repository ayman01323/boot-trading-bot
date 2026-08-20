from __future__ import annotations

"""Attach governed source intelligence to the existing Strategy Lab report.

This module is intentionally reporting/research-only.  It does not install packages,
open exchange accounts, create RPC sessions, submit transactions, or modify LIVE
configuration.  It extends the research payload so AI reviewers and operators can see
the approved catalogue and the rules for proposing future sources.
"""

from . import strategy_lab_research as _research
from .strategy_source_catalog import CURATED_STRATEGY_SOURCES, SOURCE_DISCOVERY_POLICY, source_catalogue

# Preserve the original list for backwards compatibility with existing tests/callers,
# while adding the richer catalogue without duplicating names already present.
_existing = {str(row.get("tool") or "").strip().lower() for row in _research.PUBLIC_RESEARCH_TOOLS}
for _row in CURATED_STRATEGY_SOURCES:
    if str(_row.get("tool") or "").strip().lower() not in _existing:
        _research.PUBLIC_RESEARCH_TOOLS.append(dict(_row))
        _existing.add(str(_row.get("tool") or "").strip().lower())

_PREV_BUILD = _research.build_research_report


def _build_with_source_governance(app) -> dict:
    report = _PREV_BUILD(app)
    report["source_catalogue"] = source_catalogue()
    report["source_discovery_policy"] = dict(SOURCE_DISCOVERY_POLICY)
    report["ai_source_discovery_instruction"] = (
        "A separate three-agent source-research cycle may search the public web for new primary/raw data, official APIs/"
        "WebSockets, reputable open-source quant/backtesting/execution frameworks and academic research. Prefer canonical "
        "publisher/project documentation; do not use influencers, anonymous signal sellers or unverifiable marketing claims. "
        "A new source is research-approved only after at least two independent agents support it and GPT Master reconciles "
        "the evidence. Approval never authorises package installation, third-party code execution, exchange credentials, "
        "transaction submission or LIVE trading."
    )
    return report


_research.build_research_report = _build_with_source_governance
