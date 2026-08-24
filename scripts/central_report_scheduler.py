from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from decimal import Decimal
from pathlib import Path

from learnerbot.config import AppSettings
from learnerbot import monitor_factory_pipeline as pipeline
from learnerbot import strategy_lab
from scripts import monitor_factory_operations as ops

AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")
NON_GPT_REVIEWERS = tuple(a for a in AGENTS if a != "gpt")
FIELD_RE = re.compile(r"(?im)^([A-Z_]+):\s*(.*)$")
DISPOSITION_RE = re.compile(r"(?im)^DAILY_ENGINEERING_DISPOSITION:\s*(NO_ACTION|FACTORY_REVIEW)\s*$")
WEEKLY_RE = re.compile(r"(?im)^WEEKLY_MONITOR_STATUS:\s*(KEEP|IMPROVEMENT_RECOMMENDED|HUMAN_APPROVAL_REQUIRED)\s*$")


def _fields(text: str) -> dict[str, str]:
    return {m.group(1).upper(): " ".join(m.group(2).split()) for m in FIELD_RE.finditer(str(text or ""))}


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _write(app, name: str, payload: dict) -> None:
    root = Path(app.data_dir) / "monitor_factory"
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def trade_strategy_economics(app) -> dict:
    """Cheap deterministic priority monitor. It never trades or changes strategy state."""
    now = int(time.time())
    portfolio = strategy_lab.portfolio_report(app)
    kpis = pipeline._portfolio_kpis(portfolio)
    trades = int(kpis.get("trades") or 0)
    gross_profit = _decimal(kpis.get("gross_profit"))
    gross_loss = _decimal(kpis.get("gross_loss"))
    net_profit = _decimal(kpis.get("net_profit"))
    profit_factor = _decimal(kpis.get("profit_factor"))
    wins = int(kpis.get("wins") or 0)
    losses = int(kpis.get("losses") or 0)

    primary_ok = net_profit > 0 and profit_factor > Decimal("1")
    value_ok = gross_profit > gross_loss
    count_ok = wins > losses
    findings = []

    if trades >= 8 and (not primary_ok or not value_ok):
        finding = pipeline.record_finding(
            app,
            lane="STRATEGY",
            finding_type="PROBLEM",
            classification="STRATEGY",
            severity="P1" if net_profit < 0 or gross_profit < gross_loss else "P2",
            title="Trade-strategy economics are below the value-weighted target",
            scope="ALL_STRATEGIES",
            evidence={"portfolio_kpis": kpis},
            recommendation=(
                "Strategy Factory should identify which strategies, market conditions, or execution leakage cause losing value to approach or exceed winning value, then test bounded SHADOW improvements. Do not loosen liquidity, sellability, simulation, risk, slippage or execution gates merely to increase trade count."
            ),
            acceptance_test=(
                "Fresh adequately sampled evidence must show positive net P&L after recorded costs, profit factor above 1, and gross winning value above gross losing value without an execution-safety regression."
            ),
            now=now,
        )
        pipeline.queue_finding(app, finding, now=now)
        findings.append(finding)
    elif trades >= 8 and not count_ok:
        finding = pipeline.record_finding(
            app,
            lane="STRATEGY",
            finding_type="OPPORTUNITY",
            classification="STRATEGY",
            severity="P3",
            title="Win count is weaker than loss count despite acceptable value economics",
            scope="ALL_STRATEGIES",
            evidence={"portfolio_kpis": kpis},
            recommendation="Investigate loss frequency in SHADOW only. Reject any change that weakens net P&L, profit factor, or winning-value dominance.",
            acceptance_test="Win count may improve only if positive net P&L, profit factor and winning-value dominance are preserved or improved.",
            now=now,
        )
        pipeline.queue_finding(app, finding, now=now)
        findings.append(finding)

    out = {
        "schema_version": 1,
        "mode": "TRADE_STRATEGY_ECONOMICS",
        "generated_epoch": now,
        "portfolio_kpis": kpis,
        "target": {
            "primary": "positive net P&L after recorded costs and profit factor > 1",
            "value": "gross winning value > gross losing value",
            "supporting": "winning trade count > losing trade count",
        },
        "primary_pass": primary_ok,
        "winning_value_exceeds_losing_value": value_ok,
        "win_count_exceeds_loss_count": count_ok,
        "findings": findings,
        "next_destination": "STRATEGY_FACTORY_REVIEW" if findings else "NO_ACTION",
        "authority": "MONITOR_AND_ESCALATE_ONLY",
        "model_calls": 0,
        "changes_trading_state": False,
    }
    _write(app, "trade_strategy_economics_latest.json", out)
    return out


def factory_review(app, *, limit: int = 5) -> dict:
    # Factory is the single AI action/adjudication hub. Every configured agent is
    # invited only when a real queued package exists; GPT remains final adjudicator.
    original = ops._panel_for
    try:
        ops._panel_for = lambda package: list(AGENTS)
        out = ops.factory_hourly(app, limit=max(1, min(int(limit), 5)))
    finally:
        ops._panel_for = original
    out["mode"] = "STRATEGY_FACTORY_REVIEW"
    out["all_available_agents_invited_when_package_exists"] = True
    out["empty_queue_model_calls"] = 0 if int(out.get("packages_considered") or 0) == 0 else None
    out["master"] = "gpt"
    _write(app, "factory_review_latest.json", out)
    return out


def engineering_rotation(app) -> dict:
    now = int(time.time())
    root = Path(__file__).resolve().parents[1]
    engineering = pipeline.run_engineering_monitor(app, now=now)
    checks = ops._repo_checks(root)
    rotation_slot = now // (48 * 60 * 60)
    reviewer = NON_GPT_REVIEWERS[rotation_slot % len(NON_GPT_REVIEWERS)]
    subject = f"Rotating Engineering Review {time.strftime('%Y-%m-%d', time.gmtime(now))}"
    thread = f"rotating-eng-{rotation_slot}"
    reviewer_response = asyncio.run(
        ops._ask(reviewer, ops._daily_prompt(reviewer, engineering, checks), subject=subject, thread_id=thread)
    )

    gpt_prompt = f"""You are GPT, MASTER validator of one rotating engineering-agent report.

Independently validate the deterministic engineering evidence and reviewer response. Do not vote with the reviewer. If no material engineering defect or evidence gap is supported, choose NO_ACTION. If supported, choose FACTORY_REVIEW so it is queued to the central Strategy Factory Review. Do not edit, deploy, trade, alter LIVE/capital/risk/wallet/signing, or weaken any safety gate.

End with exactly:
DAILY_ENGINEERING_DISPOSITION: NO_ACTION|FACTORY_REVIEW
DAILY_ENGINEERING_TITLE: <short title or NONE>
DAILY_ENGINEERING_CLASSIFICATION: EXECUTION|INFRASTRUCTURE|DATA
DAILY_ENGINEERING_SEVERITY: P1|P2|P3|INFO
DAILY_ENGINEERING_RECOMMENDATION: <bounded recommendation or NONE>

ROTATING REVIEWER: {reviewer}
REVIEWER RESPONSE:
{str(reviewer_response.get('body') or '')[:7000]}

DETERMINISTIC EVIDENCE:
{json.dumps({'engineering_monitor': engineering, 'repo_checks': checks}, ensure_ascii=False, default=str)[:9000]}
"""
    gpt_response = asyncio.run(ops._ask("gpt", gpt_prompt, subject=subject, thread_id=thread))
    body = str(gpt_response.get("body") or "")
    disposition = DISPOSITION_RE.search(body)
    fields = _fields(body)
    finding = None
    if disposition and disposition.group(1).upper() == "FACTORY_REVIEW":
        classification = fields.get("DAILY_ENGINEERING_CLASSIFICATION", "DATA").upper()
        if classification not in {"EXECUTION", "INFRASTRUCTURE", "DATA"}:
            classification = "DATA"
        severity = fields.get("DAILY_ENGINEERING_SEVERITY", "P3").upper()
        if severity not in {"P1", "P2", "P3", "INFO"}:
            severity = "P3"
        finding = pipeline.record_finding(
            app,
            lane="ENGINEERING",
            finding_type="PROBLEM",
            classification=classification,
            severity=severity,
            title=fields.get("DAILY_ENGINEERING_TITLE", "Rotating engineering review requested Factory analysis")[:300],
            scope="ROTATING_AI_ENGINEERING_REVIEW",
            source_version=str(rotation_slot),
            evidence={
                "rotating_reviewer": reviewer,
                "review": reviewer_response,
                "gpt_master_review": gpt_response,
                "engineering_monitor": engineering,
                "repo_checks": checks,
            },
            recommendation=fields.get("DAILY_ENGINEERING_RECOMMENDATION", "Investigate with objective evidence before any change")[:1800],
            acceptance_test="Any corrective proposal must be supported by reproducible evidence and preserve all LIVE, capital, wallet/signing and execution-safety controls.",
            now=now,
        )
        pipeline.queue_finding(app, finding, now=now)

    out = {
        "schema_version": 1,
        "mode": "ROTATING_AI_ENGINEERING_REVIEW",
        "generated_epoch": now,
        "reviewer": reviewer,
        "review": reviewer_response,
        "gpt_master_review": gpt_response,
        "finding": finding,
        "repo_checks": checks,
        "next_destination": "STRATEGY_FACTORY_REVIEW" if finding else "NO_ACTION",
        "no_repository_mutation_by_reviewer": True,
    }
    _write(app, "engineering_rotation_latest.json", out)
    return out


def weekly_joint(app) -> dict:
    root = Path(__file__).resolve().parents[1]
    out = ops.weekly_joint(app, root)
    final_body = str(((out.get("final") or {}).get("body") or ""))
    match = WEEKLY_RE.search(final_body)
    status = match.group(1).upper() if match else "KEEP"
    finding = None
    if status != "KEEP":
        now = int(time.time())
        finding = pipeline.record_finding(
            app,
            lane="ENGINEERING" if status == "HUMAN_APPROVAL_REQUIRED" else "STRATEGY",
            finding_type="PROBLEM" if status == "HUMAN_APPROVAL_REQUIRED" else "OPPORTUNITY",
            classification="DATA" if status == "HUMAN_APPROVAL_REQUIRED" else "RESEARCH",
            severity="P2" if status == "HUMAN_APPROVAL_REQUIRED" else "P3",
            title="Seven-agent joint review identified an item for Strategy Factory adjudication",
            scope="STRATEGY_FACTORY_ENGINEERING_OPERATING_MODEL",
            source_version=time.strftime("%Y-%m-%d", time.gmtime(now)),
            evidence={"weekly_review": out},
            recommendation=final_body[:1800] or "Strategy Factory should adjudicate the joint-review finding.",
            acceptance_test="Factory must resolve the item using objective evidence; protected engineering/LIVE changes remain human-approved.",
            now=now,
        )
        pipeline.queue_finding(app, finding, now=now)
    out["weekly_status"] = status
    out["factory_finding"] = finding
    out["next_destination"] = "STRATEGY_FACTORY_REVIEW" if finding else "NO_ACTION"
    out["master"] = "gpt"
    _write(app, "weekly_joint_latest.json", out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Central cost-effective report scheduler workers")
    parser.add_argument("mode", choices=(
        "trade-strategy-economics",
        "observe-engineering",
        "observe-strategy",
        "factory-review",
        "engineering-rotation",
        "weekly-joint",
        "status",
    ))
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    app = AppSettings.load()
    if args.mode == "trade-strategy-economics":
        result = trade_strategy_economics(app)
    elif args.mode == "observe-engineering":
        result = pipeline.run_engineering_monitor(app)
    elif args.mode == "observe-strategy":
        result = pipeline.run_strategy_monitor(app)
    elif args.mode == "factory-review":
        result = factory_review(app, limit=args.limit)
    elif args.mode == "engineering-rotation":
        result = engineering_rotation(app)
    elif args.mode == "weekly-joint":
        result = weekly_joint(app)
    else:
        result = pipeline.status_summary(app)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
