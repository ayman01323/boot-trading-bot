from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from learnerbot.config import AppSettings
from learnerbot import monitor_factory_pipeline as pipeline
from learnerbot import shadow_strategy_executor
from learnerbot import strategy_canary
from learnerbot import strategy_lab
from scripts import monitor_factory_operations as operations


TARGET_STRATEGIES = (
    "Cross Venue Net Arbitrage",
    "Liquidity Confirmed Momentum",
    "Dislocation Mean Reversion",
    "Flow Acceleration",
    "New Liquidity Quality",
    "Learned Route Replication",
    "Forecasted Positive Net Edge",
)

STRATEGY_FAMILIES = {
    "Cross Venue Net Arbitrage": "ARBITRAGE",
    "Liquidity Confirmed Momentum": "MOMENTUM",
    "Dislocation Mean Reversion": "MEAN_REVERSION",
    "Flow Acceleration": "FLOW",
    "New Liquidity Quality": "NEW_MARKET",
    "Learned Route Replication": "LEARNED_PATTERN",
    "Forecasted Positive Net Edge": "FORECAST",
}

PROMOTED_REAL_MONEY_STAGES = {"CANARY", "PROBATION", "ACTIVE"}
BRIDGE_ROOT = Path("/var/tmp/boot")
RUNNER_REVIEW_ROOT = BRIDGE_ROOT / "monitor_factory_runner"
PRODUCTION_BRIDGES = {
    "trading_funnel": BRIDGE_ROOT / "trading_funnel_master.json",
    "evm_selector": BRIDGE_ROOT / "evm_leader_selector.json",
    "solana_selector": BRIDGE_ROOT / "solana_leader_selector.json",
    "evm_reconstruction": BRIDGE_ROOT / "evm_reconstruction_status.json",
    "strategy_factory_leader_research": BRIDGE_ROOT / "strategy_factory_leader_research.json",
}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _production_data_accessible(app) -> bool:
    try:
        return (
            Path(app.data_dir).is_dir()
            and Path(app.csv_dir).is_dir()
            and os.access(str(app.data_dir), os.R_OK | os.W_OK)
            and os.access(str(app.csv_dir), os.R_OK)
        )
    except Exception:
        return False


def _runner_app(app) -> AppSettings:
    RUNNER_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    return AppSettings(
        root=Path(app.root),
        csv_dir=BRIDGE_ROOT,
        data_dir=RUNNER_REVIEW_ROOT,
        telegram_bot_token="",
        telegram_chat_ids=[],
        etherscan_api_key="",
    )


def _target_rows(portfolio: dict) -> list[dict]:
    wanted = {name.casefold() for name in TARGET_STRATEGIES}
    out = []
    for row in (portfolio or {}).get("strategies") or []:
        name = str((row or {}).get("name") or "").strip()
        if name.casefold() not in wanted:
            continue
        metrics = dict((row or {}).get("metrics") or {})
        out.append({
            "strategy_id": str((row or {}).get("strategy_id") or ""),
            "name": name,
            "family": str((row or {}).get("family") or ""),
            "status": str((row or {}).get("status") or ""),
            "previous_status": str((row or {}).get("previous_status") or ""),
            "action": str((row or {}).get("action") or ""),
            "reason": str((row or {}).get("reason") or "")[:600],
            "metrics": {
                "windows": int(metrics.get("windows") or 0),
                "opportunities": int(metrics.get("opportunities") or 0),
                "eligible_opportunities": int(metrics.get("eligible_opportunities") or 0),
                "trades": int(metrics.get("trades") or 0),
                "wins": int(metrics.get("wins") or 0),
                "losses": int(metrics.get("losses") or 0),
                "net_profit": str(metrics.get("net_profit") or "0"),
                "profit_factor": str(metrics.get("profit_factor") or "0"),
                "execution_failures": int(metrics.get("execution_failures") or 0),
            },
        })
    return sorted(out, key=lambda row: row["name"])


def _bridge_target_rows() -> list[dict]:
    return [
        {
            "strategy_id": "",
            "name": name,
            "family": STRATEGY_FAMILIES[name],
            "status": "SHADOW",
            "previous_status": "",
            "action": "REVIEW_NOW",
            "reason": "Production Strategy Lab database is intentionally not readable by the GitHub runner; use sanitised production bridges plus repository implementation evidence.",
            "metrics": {
                "windows": None,
                "opportunities": None,
                "eligible_opportunities": None,
                "trades": None,
                "wins": None,
                "losses": None,
                "net_profit": None,
                "profit_factor": None,
                "execution_failures": None,
            },
        }
        for name in TARGET_STRATEGIES
    ]


def _canary_rows(app) -> list[dict]:
    try:
        rows = strategy_canary.canary_status(app)
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"[:500]}]
    out = []
    wanted = {name.casefold() for name in TARGET_STRATEGIES}
    for row in rows:
        strategy = str((row or {}).get("strategy") or "").strip()
        if strategy.casefold() not in wanted:
            continue
        out.append({
            "strategy": strategy,
            "stage": str((row or {}).get("stage") or "").upper(),
            "trades": int((row or {}).get("trades") or 0),
            "wins": int((row or {}).get("wins") or 0),
            "losses": int((row or {}).get("losses") or 0),
            "net_profit": str((row or {}).get("net_profit") or "0"),
            "execution_failures": int((row or {}).get("execution_failures") or 0),
            "source_commit": str((row or {}).get("source_commit") or ""),
        })
    return out


def _shadow_scorecard(app, now: int) -> dict:
    try:
        return shadow_strategy_executor.scorecard(app, since=now - 24 * 3600, now=now)
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:600]}


def _base_evidence(now: int, operator_urgent: bool) -> dict:
    return {
        "schema_version": 1,
        "generated_epoch": now,
        "operator_urgent_no_trade_report": bool(operator_urgent),
        "operator_report_note": (
            "Operator reports the bot has not produced useful live trading for multiple days. Treat this as an escalation signal to verify, not as deterministic profitability evidence."
            if operator_urgent else ""
        ),
        "objective": (
            "Rapidly identify the fastest evidence-backed path to at least one legitimate real-funds CANARY on EVM and the missing implementation/evidence needed for Solana, without lowering execution, liquidity, sellability, simulation, wallet, reserve, signing, reconciliation, or loss-containment protections."
        ),
        "known_architecture_boundaries": {
            "evm_canary_policy_present": callable(getattr(strategy_canary, "route_canary_policy", None)),
            "solana_market_native_canary_export_present": any(
                "solana" in name.casefold() and "canary" in name.casefold()
                for name in dir(strategy_canary)
            ),
            "gpt_final_adjudicator": True,
            "factory_p1_panel_size": 7,
            "live_safety_bypass_allowed": False,
            "profit_guarantee_possible": False,
        },
        "required_agent_output": {
            "per_strategy_per_chain": [
                "current_stage_and_real_blocker",
                "IMPLEMENT_NOW|SHADOW_MORE|REWORK|REJECT",
                "exact_missing_code_or_evidence",
                "minimum_falsifiable_test",
                "canary_readiness",
                "fastest_safe_next_step",
            ],
            "gpt_final": (
                "Rank the top two fastest safe candidates for EVM and for Solana. Distinguish missing implementation from missing evidence. If protected runtime/LIVE code is required, mark HUMAN_APPROVAL_REQUIRED. Do not lower a common safety gate merely to create trade count."
            ),
        },
    }


def build_full_evidence(app, *, now: int, operator_urgent: bool) -> dict:
    normal_monitor = pipeline.run_strategy_monitor(app, now=now)
    strategy_lab.seed_creative_hypotheses(app)
    portfolio = strategy_lab.portfolio_report(app)
    targets = _target_rows(portfolio)
    canary = _canary_rows(app)
    promoted = {
        str(row.get("strategy") or "").casefold()
        for row in canary
        if str(row.get("stage") or "").upper() in PROMOTED_REAL_MONEY_STAGES
    }
    found = {str(row.get("name") or "").casefold() for row in targets}
    evidence = _base_evidence(now, operator_urgent)
    evidence.update({
        "evidence_mode": "FULL_PRODUCTION_DATA",
        "target_strategies": targets,
        "missing_target_strategies": [name for name in TARGET_STRATEGIES if name.casefold() not in found],
        "strategies_not_in_real_money_validation": [name for name in TARGET_STRATEGIES if name.casefold() not in promoted],
        "target_strategy_lab_trades": sum(int((row.get("metrics") or {}).get("trades") or 0) for row in targets),
        "canary_state": canary,
        "shadow_scorecard_24h": _shadow_scorecard(app, now),
        "normal_strategy_monitor": {
            "portfolio_kpis": normal_monitor.get("portfolio_kpis") or {},
            "findings": normal_monitor.get("findings") or [],
            "packages_queued_or_refreshed": normal_monitor.get("packages_queued_or_refreshed") or [],
        },
    })
    return evidence


def build_bridge_evidence(*, now: int, operator_urgent: bool) -> dict:
    bridges = {name: _read_json(path) for name, path in PRODUCTION_BRIDGES.items()}
    freshness = {}
    for name, value in bridges.items():
        ts = int((value or {}).get("generated_epoch") or (value or {}).get("updated_epoch") or 0)
        freshness[name] = {"available": bool(value), "generated_epoch": ts, "age_seconds": max(0, now - ts) if ts else None}
    evidence = _base_evidence(now, operator_urgent)
    evidence.update({
        "evidence_mode": "SANITISED_PRODUCTION_BRIDGES",
        "security_boundary": "GitHub runner cannot read /root production databases; current service-generated 0644 /var/tmp/boot snapshots are used instead.",
        "target_strategies": _bridge_target_rows(),
        "missing_target_strategies": [],
        "strategies_not_in_real_money_validation": list(TARGET_STRATEGIES),
        "target_strategy_lab_trades": None,
        "canary_state": [],
        "canary_state_note": "Protected production canary database is not exposed to the runner; repository code still defines EVM CANARY/PROBATION/ACTIVE and no independent Solana market-native canary adapter.",
        "shadow_scorecard_24h": {"available": False, "reason": "protected production database; use current funnel bridges"},
        "normal_strategy_monitor": {"available": False, "reason": "protected production database; urgent review uses service-exported current funnel evidence"},
        "production_bridge_freshness": freshness,
        "production_bridges": bridges,
    })
    return evidence


def build_evidence(app, *, now: int | None = None, operator_urgent: bool = False) -> dict:
    now = int(now or time.time())
    if _production_data_accessible(app):
        return build_full_evidence(app, now=now, operator_urgent=operator_urgent)
    return build_bridge_evidence(now=now, operator_urgent=operator_urgent)


def needs_urgent_review(evidence: dict) -> bool:
    if evidence.get("operator_urgent_no_trade_report"):
        return True
    if evidence.get("missing_target_strategies"):
        return True
    waiting = list(evidence.get("strategies_not_in_real_money_validation") or [])
    return bool(waiting)


def _finding_source_version(now: int, *, force: bool) -> str:
    return time.strftime("urgent-%Y%m%d%H", time.gmtime(now)) if force else time.strftime("watch-%Y%m%d", time.gmtime(now))


def queue_urgent_package(app, evidence: dict, *, now: int, force: bool) -> dict:
    finding = pipeline.record_finding(
        app,
        lane="STRATEGY",
        finding_type="PROBLEM",
        classification="STRATEGY",
        severity="P1",
        title="Urgent non-leader strategy pipeline is stalled before real-funds validation",
        scope="SOLANA+EVM:NON_LEADER_PORTFOLIO",
        strategy_id="non-leader-portfolio",
        source_version=_finding_source_version(now, force=force),
        evidence=evidence,
        recommendation=(
            "All seven Strategy Factory agents must independently review every listed strategy on EVM and Solana, identify the exact promotion blocker, and rank the fastest safe path to a bounded real-funds CANARY. GPT is final adjudicator. Separate strategy/market gaps from execution/data/implementation gaps; preserve all common safety gates."
        ),
        acceptance_test=(
            "For each listed strategy and each chain, produce an evidence-backed stage/blocker/action matrix. At least one candidate may advance only through the existing exact-source approval and CANARY path with current executable positive edge and normal quote/simulation/liquidity/sellability safeguards. Subsequent promotion requires realised positive net economics; no trade quota or profit claim substitutes for evidence."
        ),
        now=now,
    )
    return pipeline.queue_finding(app, finding, now=now)


async def force_review(app, package: dict) -> dict:
    return await operations.review_package(app, package)


def run(mode: str) -> dict:
    production_app = AppSettings.load()
    now = int(time.time())
    force = str(mode).lower() == "force"
    evidence = build_evidence(production_app, now=now, operator_urgent=force)
    review_app = production_app if _production_data_accessible(production_app) else _runner_app(production_app)
    if not needs_urgent_review(evidence):
        return {
            "schema_version": 1,
            "mode": str(mode).upper(),
            "generated_epoch": now,
            "action": "NO_URGENT_PACKAGE",
            "reason": "All target strategies have entered a real-money validation stage and no target strategy is missing.",
            "evidence": evidence,
        }

    package = queue_urgent_package(review_app, evidence, now=now, force=force)
    out: dict[str, Any] = {
        "schema_version": 1,
        "mode": str(mode).upper(),
        "generated_epoch": now,
        "action": "QUEUED_P1_FACTORY_PACKAGE",
        "package": package,
        "evidence": evidence,
        "review_storage": str(review_app.data_dir),
    }
    if force:
        out["action"] = "REVIEWED_NOW_BY_SEVEN_AGENT_FACTORY"
        out["review"] = asyncio.run(force_review(review_app, package))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Urgent Strategy Monitor/Factory escalation for stalled non-leader strategies")
    parser.add_argument("mode", choices=("force", "watch"), nargs="?", default="watch")
    args = parser.parse_args()
    print(json.dumps(run(args.mode), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
