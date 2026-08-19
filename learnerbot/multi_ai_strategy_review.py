from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

DEFAULT_OPENAI_REVIEW_MODEL = "gpt-5.6-terra"
DEFAULT_OPENAI_SYNTH_MODEL = "gpt-5.6"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_REVIEW = """
You are one independent reviewer in a multi-model audit of an automated cryptocurrency
trading system. Your task is operational and quantitative risk review, not investment
advice. Do not recommend buying or selling any particular asset. Do not claim or imply
that profit can be guaranteed.

Use only the supplied sanitised report. Separate:
1. OBSERVED facts directly supported by the report;
2. INFERRED causes that are plausible but not proven;
3. PROPOSED shadow experiments.

Optimise for durable net profitability after all costs, not for win rate alone. Pay
particular attention to gross-profit/gross-loss ratio, realised net P&L, execution
failures, latency, fees, slippage, exit failures, data quality, sample size, drawdown,
selection bias and over-trading.

Any proposed strategy or configuration change must be SHADOW_ONLY until independently
tested. Do not authorise live deployment. Return JSON only, with no markdown fences.
""".strip()

SYSTEM_SYNTH = """
You are the evidence-weighting synthesiser for independent AI reviews of a trading-bot
operational report. Do not use simple majority voting. Prefer hard metrics over model
opinion. Reject recommendations that are unsupported, contradictory, duplicative, or
likely to improve win rate while worsening net P&L.

A recommendation may become a CODE_CHANGE_CANDIDATE only if:
- at least two independent reviewers support the same underlying change, OR the source
  report directly proves a concrete implementation defect;
- the recommendation has an explicit downside/risk analysis;
- there is a falsifiable shadow test;
- there is enough data to evaluate that test;
- no recommendation requires buying/selling a named asset;
- live deployment remains disabled.

You may recommend PAUSE_AND_FIX for a proven operational defect even without two-model
agreement. Never promise profit. Never auto-deploy live trading changes. Output JSON
only, with no markdown fences.
""".strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_fences(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict:
    text = _strip_fences(text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(text[start : end + 1])
        if isinstance(value, dict):
            return value
    raise RuntimeError("model response was not a JSON object")


def _extract_openai_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    raise RuntimeError("OpenAI response contained no output text")


def _extract_gemini_text(payload: dict) -> str:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
        text = "".join(chunks).strip()
        if text:
            return text
    raise RuntimeError("Gemini response contained no output text")


def _extract_anthropic_text(payload: dict) -> str:
    chunks = []
    for part in payload.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("Anthropic response contained no output text")
    return text


def _review_prompt(report: dict) -> str:
    return f"""
Review the following sanitised hourly/rolling trading-bot forensics report.

Required JSON shape:
{{
  "status": "HEALTHY|WATCH|DEGRADED",
  "confidence_0_100": 0,
  "executive_summary": "...",
  "observed": [
    {{"fact": "...", "evidence_path": "JSON path or metric", "importance": "LOW|MEDIUM|HIGH"}}
  ],
  "inferred_root_causes": [
    {{"cause": "...", "support": "...", "confidence_0_100": 0}}
  ],
  "recommendations": [
    {{
      "title": "...",
      "category": "EXECUTION|EXIT|SELECTION|RISK|LATENCY|ACCOUNTING|DATA|INFRASTRUCTURE",
      "priority": "LOW|MEDIUM|HIGH|CRITICAL",
      "rationale": "...",
      "expected_effect": "...",
      "downside_risk": "...",
      "evidence_required": "...",
      "shadow_test": "...",
      "minimum_observation": "...",
      "live_mode": false
    }}
  ],
  "rejected_ideas": [
    {{"idea": "...", "reason": "..."}}
  ],
  "evidence_gaps": ["..."],
  "no_live_changes": true
}}

Rules:
- Calculate or interpret profit factor where the report permits it.
- "More winning trades than losing trades" is not enough. Gross profit must exceed gross
  loss after execution costs; distinguish trade count from money-weighted outcome.
- Identify whether losses are driven by selection, execution, exits, fees, latency,
  accounting/data faults, or insufficient sample size.
- Do not invent missing slippage, gas, fee or latency values.
- Prefer reducing avoidable loss magnitude and execution leakage over increasing trade
  frequency.
- Every recommendation must contain a falsifiable shadow test.

SOURCE REPORT:
{json.dumps(report, indent=2, sort_keys=True, default=str)}
""".strip()


def _openai_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    body = {
        "model": model,
        "store": False,
        "instructions": system,
        "input": prompt,
    }
    r = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    return _parse_json(_extract_openai_text(payload)), {
        "provider": "openai",
        "model": payload.get("model") or model,
        "response_id": payload.get("id"),
        "usage": payload.get("usage") or {},
    }


def _gemini_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    url = f"{GEMINI_BASE}/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    r = requests.post(
        url,
        params={"key": key},
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    usage = payload.get("usageMetadata") or {}
    return _parse_json(_extract_gemini_text(payload)), {
        "provider": "gemini",
        "model": model,
        "usage": usage,
    }


def _anthropic_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    body = {
        "model": model,
        "max_tokens": 5000,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    return _parse_json(_extract_anthropic_text(payload)), {
        "provider": "anthropic",
        "model": payload.get("model") or model,
        "response_id": payload.get("id"),
        "usage": payload.get("usage") or {},
    }


def _provider_result(name: str, fn, *args, **kwargs) -> dict:
    started = time.time()
    try:
        review, meta = fn(*args, **kwargs)
        if review.get("no_live_changes") is not True:
            raise RuntimeError("review omitted mandatory no_live_changes=true gate")
        return {
            "ok": True,
            "provider": name,
            "elapsed_seconds": round(time.time() - started, 3),
            "review": review,
            "meta": meta,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": name,
            "elapsed_seconds": round(time.time() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _extract_objective_metrics(report: dict) -> dict:
    perf = (((report.get("solana_live") or {}).get("performance")) or {})
    return {
        "closed_trades": perf.get("closed_trades"),
        "wins": perf.get("wins"),
        "losses": perf.get("losses"),
        "gross_profit": perf.get("gross_profit_sol"),
        "gross_loss": perf.get("gross_loss_sol"),
        "net": perf.get("net_sol"),
        "profit_factor": perf.get("profit_factor"),
        "profit_amount_exceeds_loss_amount": perf.get("profit_amount_exceeds_loss_amount"),
        "largest_win": perf.get("largest_win_sol"),
        "largest_loss": perf.get("largest_loss_sol"),
        "exit_reason_counts": perf.get("exit_reason_counts") or {},
        "exit_circuit_status_counts": (report.get("solana_live") or {}).get("exit_circuit_status_counts") or {},
    }


def _synthesis_prompt(report: dict, provider_results: list[dict]) -> str:
    successful = [
        {
            "provider": r.get("provider"),
            "review": r.get("review"),
            "meta": r.get("meta"),
        }
        for r in provider_results
        if r.get("ok")
    ]
    objective = _extract_objective_metrics(report)
    return f"""
Synthesize the independent reviews below against the original objective metrics.

Required JSON shape:
{{
  "status": "NO_CHANGE|SHADOW_TEST|PAUSE_AND_FIX|CODE_CHANGE_CANDIDATE",
  "executive_summary": "...",
  "objective_metrics": {{}},
  "reviewers_available": 0,
  "accepted_recommendations": [
    {{
      "title": "...",
      "category": "...",
      "supporting_providers": ["..."],
      "source_evidence": ["..."],
      "why_accepted": "...",
      "downside_risk": "...",
      "shadow_test": "...",
      "promotion_gate": "...",
      "implementation_type": "CONFIG|CODE|RESEARCH"
    }}
  ],
  "rejected_recommendations": [
    {{
      "title": "...",
      "providers": ["..."],
      "reason_rejected": "..."
    }}
  ],
  "implementation_candidate": false,
  "implementation_scope": [
    {{
      "area": "...",
      "change_goal": "...",
      "must_not_change": "..."
    }}
  ],
  "evidence_gates": {{
    "minimum_independent_reviewers": 2,
    "require_positive_net_after_costs": true,
    "require_profit_factor_above_one": true,
    "require_shadow_validation": true,
    "require_no_critical_execution_faults": true
  }},
  "live_auto_deploy": false,
  "draft_pr_only": true
}}

Important:
- If fewer than two independent reviewers succeeded, implementation_candidate MUST be false
  unless the source report itself proves a concrete operational defect; even then choose
  PAUSE_AND_FIX rather than autonomous strategy optimisation.
- A strategy is not successful merely because wins > losses.
- Do not accept a recommendation just because two models use similar wording. Identify
  whether they rely on the same hard evidence.
- A CODE_CHANGE_CANDIDATE is permission to prepare a tested draft PR only, never to merge
  or deploy it automatically.
- If data is sparse, prefer NO_CHANGE or SHADOW_TEST.
- Preserve the source objective metrics exactly; do not recalculate using invented values.

OBJECTIVE METRICS:
{json.dumps(objective, indent=2, sort_keys=True, default=str)}

INDEPENDENT REVIEWS:
{json.dumps(successful, indent=2, sort_keys=True, default=str)}
""".strip()


def _validate_consensus(consensus: dict, successful_reviewers: int) -> None:
    if consensus.get("live_auto_deploy") is not False:
        raise RuntimeError("consensus failed live_auto_deploy=false safety gate")
    if consensus.get("draft_pr_only") is not True:
        raise RuntimeError("consensus failed draft_pr_only=true safety gate")
    if successful_reviewers < 2 and consensus.get("implementation_candidate") is True:
        if consensus.get("status") != "PAUSE_AND_FIX":
            raise RuntimeError("implementation candidate requires two independent reviewers")


def run(report_path: Path, output_dir: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_text = _canonical(report)
    prompt = _review_prompt(report)

    openai_model = os.environ.get("OPENAI_REVIEW_MODEL", DEFAULT_OPENAI_REVIEW_MODEL).strip()
    gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    claude_model = os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL).strip()
    synth_model = os.environ.get("OPENAI_SYNTH_MODEL", DEFAULT_OPENAI_SYNTH_MODEL).strip()

    results = [
        _provider_result(
            "openai",
            _openai_call,
            prompt,
            model=openai_model,
            system=SYSTEM_REVIEW,
        ),
        _provider_result(
            "gemini",
            _gemini_call,
            prompt,
            model=gemini_model,
            system=SYSTEM_REVIEW,
        ),
        _provider_result(
            "anthropic",
            _anthropic_call,
            prompt,
            model=claude_model,
            system=SYSTEM_REVIEW,
        ),
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "source_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    for result in results:
        (output_dir / f"review_{result['provider']}.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    successful = sum(1 for r in results if r.get("ok"))
    consensus_result: dict
    if os.environ.get("OPENAI_API_KEY", "").strip() and successful:
        try:
            consensus, meta = _openai_call(
                _synthesis_prompt(report, results),
                model=synth_model,
                system=SYSTEM_SYNTH,
                timeout=150,
            )
            _validate_consensus(consensus, successful)
            consensus_result = {
                "ok": True,
                "consensus": consensus,
                "meta": meta,
            }
        except Exception as exc:
            consensus_result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback": {
                    "status": "NO_CHANGE",
                    "implementation_candidate": False,
                    "live_auto_deploy": False,
                    "draft_pr_only": True,
                    "reason": "synthesis failed; no automated change permitted",
                },
            }
    else:
        consensus_result = {
            "ok": False,
            "error": "OpenAI synthesis unavailable or no independent reviewer succeeded",
            "fallback": {
                "status": "NO_CHANGE",
                "implementation_candidate": False,
                "live_auto_deploy": False,
                "draft_pr_only": True,
                "reason": "insufficient independent review coverage",
            },
        }

    (output_dir / "consensus.json").write_text(
        json.dumps(consensus_result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "generated_epoch": int(time.time()),
        "source_report": str(report_path),
        "source_report_sha256": _sha256_text(report_text),
        "successful_reviewers": successful,
        "providers": [
            {
                "provider": r.get("provider"),
                "ok": bool(r.get("ok")),
                "model": ((r.get("meta") or {}).get("model")),
                "error": r.get("error"),
            }
            for r in results
        ],
        "consensus_ok": bool(consensus_result.get("ok")),
        "mode": "RESEARCH_AND_DRAFT_PR_ONLY",
        "live_auto_deploy": False,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run independent multi-AI review of a sanitised trading-bot report.")
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    args = p.parse_args(argv)

    if not args.report.exists():
        print(f"report not found: {args.report}", file=sys.stderr)
        return 2

    try:
        summary = run(args.report, args.output_dir)
    except Exception as exc:
        print(f"multi-AI review failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
