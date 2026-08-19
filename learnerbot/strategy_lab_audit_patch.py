from __future__ import annotations

from pathlib import Path

# Reporting-only composition patch. It does not execute LIVE trades or alter trading hooks.
# It enriches the existing sanitised hourly loss-forensics payload with Strategy Lab
# scorecards, cross-chain learner research, and the non-signing market-feature/shadow lane.

from . import loss_forensics_github_export as _forensics
from .shadow_strategy_executor import run_shadow_cycle
from .strategy_lab import portfolio_report, seed_creative_hypotheses
from .strategy_lab_research import build_research_report, ensure_cross_chain_scope

_PREV_BUILD = _forensics.build_loss_forensics


def _activate_cross_chain_once(app) -> dict:
    """Run the metadata migration once so later lifecycle states are never reset."""
    marker = Path(app.data_dir) / "strategy_lab_cross_chain_v1.marker"
    if marker.exists():
        return {"already_activated": True, "marker": str(marker)}
    result = ensure_cross_chain_scope(app)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("cross-chain Strategy Lab metadata activated\n", encoding="utf-8")
    result["already_activated"] = False
    result["marker"] = str(marker)
    return result


def build_loss_forensics_with_strategy_lab(app, zip_path, gpt_result=None, *, hours=_forensics.WINDOW_HOURS):
    report = _PREV_BUILD(app, zip_path, gpt_result, hours=hours)
    try:
        seeded = seed_creative_hypotheses(app)
        cross_chain = _activate_cross_chain_once(app)
        try:
            shadow_execution = run_shadow_cycle(app)
        except Exception as exc:
            shadow_execution = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
                "live_orders_submitted": 0,
                "live_auto_promotion": False,
            }
        lab = portfolio_report(app)
        research = build_research_report(app)
        lab["available"] = True
        lab["chain_scope"] = ["SOLANA", "EVM"]
        lab["new_hypotheses_seeded_this_run"] = [
            {
                "strategy_id": row.get("strategy_id"),
                "name": row.get("name"),
                "family": row.get("family"),
                "status": row.get("status"),
            }
            for row in seeded
        ]
        lab["cross_chain_activation"] = cross_chain
        lab["research"] = research
        lab["shadow_execution"] = shadow_execution
        lab["ai_review_instruction"] = (
            "Review every strategy separately across Solana and EVM. The same economic strategy family may be tested on both "
            "chain types, but each chain must use its own executable quote, fees, gas/priority cost, slippage, liquidity, "
            "sellability and latency assumptions. The market-feature/shadow-execution scorecard is non-signing: current exact "
            "quotes and simulations are useful research evidence but are NOT realised P&L and MUST NOT by themselves justify "
            "CANARY or LIVE promotion. Push the learner beyond leader-following: inspect profitable public-wallet cohorts and "
            "learned strategy_patterns for repeated behaviours shared by multiple profitable wallets; never treat one wallet "
            "as proof. Recommend public research tools when evidence is missing, including Dune for on-chain cohorts, DEX "
            "Screener for pair/liquidity/flow research, Etherscan V2 for EVM wallet history, DefiLlama for regime/protocol "
            "activity, Jupiter for Solana route/quote research, and GitHub public code search for read-only architecture ideas. "
            "Never execute untrusted third-party bot code. Suggest new falsifiable SHADOW strategies, including forecast models "
            "whose target is positive NET edge after costs rather than price direction alone. If a strong opportunity requires "
            "an asset absent from the current universe, emit an asset request with chain, asset identifier, evidence, liquidity/"
            "sellability/quote requirements and reason; do not auto-enable it. Do not reward raw trade count. Reward realised "
            "net profit after recorded costs, profit factor, loss magnitude, opportunity participation, execution quality, "
            "out-of-sample robustness and calibrated forecast quality. Never force a trade merely to satisfy activity."
        )
        report["strategy_lab"] = lab
    except Exception as exc:
        report["strategy_lab"] = {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "chain_scope": ["SOLANA", "EVM"],
            "live_auto_promote": False,
        }
    return report


_forensics.build_loss_forensics = build_loss_forensics_with_strategy_lab
