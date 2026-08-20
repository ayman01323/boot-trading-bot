from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests

OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

DEFAULT_OPENAI_REVIEW_MODEL = "gpt-5.6-terra"
DEFAULT_OPENAI_SYNTH_MODEL = "gpt-5.6"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

REVIEW_SYSTEM = """You are an independent reviewer of an automated cryptocurrency trading system.
Use only the supplied sanitised operational evidence. This is quantitative system/risk analysis,
not a recommendation to buy or sell any named asset. Never claim profit is guaranteed.
Separate observed facts from inferred causes. Optimise durable realised NET P&L after known costs,
not win count. Examine loss magnitude, profit factor, exits, execution failures, fees/slippage/latency
where actually measured, data quality, sample size, selection bias and over-trading. Every proposed
change must have a falsifiable SHADOW test. Never authorise a live trading change. Return JSON only."""

SYNTH_SYSTEM = """You are the evidence-weighting synthesiser for independent AI reviews of a trading bot.
Do not use simple majority voting. Objective measured evidence outranks model agreement. Reject
unsupported, contradictory, duplicate, or unfalsifiable recommendations. A normal code/strategy
candidate needs support from at least two independent reviewers plus an explicit downside analysis
and shadow test. A directly proven operational defect may justify PAUSE_AND_FIX with fewer reviewers,
but not autonomous strategy optimisation. Never recommend a named-asset trade, never promise profit,
and never authorise merge or live deployment. Return JSON only."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_json(text: str) -> dict:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("model response was not JSON")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("model response was not a JSON object")
    return value


def _extract_openai_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    raise RuntimeError("OpenAI returned no text")


def _extract_gemini_text(payload: dict) -> str:
    for candidate in payload.get("candidates") or []:
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
        if text:
            return text
    raise RuntimeError("Gemini returned no text")


def _extract_anthropic_text(payload: dict) -> str:
    text = "".join(
        str(part.get("text") or "")
        for part in payload.get("content") or []
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError("Claude returned no text")
    return text


def _review_prompt(report: dict) -> str:
    return """Review the source report below and return exactly one JSON object with this shape:
{
  "status": "HEALTHY|WATCH|DEGRADED",
  "confidence_0_100": 0,
  "executive_summary": "...",
  "observed": [{"fact":"...","evidence_path":"...","importance":"LOW|MEDIUM|HIGH"}],
  "inferred_root_causes": [{"cause":"...","support":"...","confidence_0_100":0}],
  "recommendations": [{
    "title":"...",
    "category":"EXECUTION|EXIT|SELECTION|RISK|LATENCY|ACCOUNTING|DATA|INFRASTRUCTURE",
    "priority":"LOW|MEDIUM|HIGH|CRITICAL",
    "rationale":"...",
    "expected_effect":"...",
    "downside_risk":"...",
    "evidence_required":"...",
    "shadow_test":"...",
    "minimum_observation":"...",
    "live_mode":false
  }],
  "rejected_ideas": [{"idea":"...","reason":"..."}],
  "evidence_gaps": ["..."],
  "no_live_changes": true
}

Rules:
- More winning trades than losing trades does NOT prove a good strategy.
- Prefer realised net P&L, gross profit versus gross loss, and profit factor.
- Distinguish selection losses from execution/exit/fee/latency losses where evidence permits.
- Do not invent missing costs or measurements.
- Every recommendation must be testable in shadow mode before any implementation.

SOURCE REPORT:
""" + json.dumps(report, indent=2, sort_keys=True, default=str)


def _openai_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    response = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "store": False, "instructions": system, "input": prompt},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return _parse_json(_extract_openai_text(payload)), {
        "model": payload.get("model") or model,
        "response_id": payload.get("id"),
        "usage": payload.get("usage") or {},
    }


def _gemini_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    response = requests.post(
        GEMINI_URL.format(model=model),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return _parse_json(_extract_gemini_text(payload)), {
        "model": model,
        "usage": payload.get("usageMetadata") or {},
    }


def _anthropic_call(prompt: str, *, model: str, system: str, timeout: int = 120) -> tuple[dict, dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    response = requests.post(
        ANTHROPIC_URL,
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": 5000,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return _parse_json(_extract_anthropic_text(payload)), {
        "model": payload.get("model") or model,
        "response_id": payload.get("id"),
        "usage": payload.get("usage") or {},
    }


def _provider_result(name: str, fn: Callable, *args, **kwargs) -> dict:
    started = time.time()
    try:
        review, meta = fn(*args, **kwargs)
        if review.get("no_live_changes") is not True:
            raise RuntimeError("review omitted mandatory no_live_changes=true gate")
        for rec in review.get("recommendations") or []:
            if rec.get("live_mode") is not False:
                raise RuntimeError("review recommendation attempted live_mode")
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
    solana = report.get("solana_live") or {}
    perf = solana.get("performance") or {}
    return {
        "closed_trades": perf.get("closed_trades"),
        "wins": perf.get("wins"),
        "losses": perf.get("losses"),
        "gross_profit": perf.get("gross_profit_sol"),
        "gross_loss": perf.get("gross_loss_sol"),
        "net": perf.get("net_sol"),
        "profit_factor": perf.get("profit_factor"),
        "profit_amount_exceeds_loss_amount": perf.get("profit_amount_exceeds_loss_amount"),
        "average_win": perf.get("average_win_sol"),
        "average_loss": perf.get("average_loss_sol"),
        "largest_win": perf.get("largest_win_sol"),
        "largest_loss": perf.get("largest_loss_sol"),
        "exit_reason_counts": perf.get("exit_reason_counts") or {},
        "exit_circuit_status_counts": solana.get("exit_circuit_status_counts") or {},
    }


def _synthesis_prompt(report: dict, reviews: list[dict]) -> str:
    successful = [
        {"provider": r["provider"], "review": r["review"], "meta": r.get("meta") or {}}
        for r in reviews
        if r.get("ok")
    ]
    return """Synthesize the objective metrics and independent reviews below. Return exactly one JSON object:
{
  "status": "NO_CHANGE|SHADOW_TEST|PAUSE_AND_FIX|CODE_CHANGE_CANDIDATE",
  "executive_summary": "...",
  "objective_metrics": {},
  "reviewers_available": 0,
  "accepted_recommendations": [{
    "title":"...",
    "category":"...",
    "supporting_providers":["..."],
    "source_evidence":["..."],
    "why_accepted":"...",
    "downside_risk":"...",
    "shadow_test":"...",
    "promotion_gate":"...",
    "implementation_type":"CONFIG|CODE|RESEARCH"
  }],
  "rejected_recommendations": [{"title":"...","providers":["..."],"reason_rejected":"..."}],
  "implementation_candidate": false,
  "implementation_scope": [{"area":"...","change_goal":"...","must_not_change":"..."}],
  "evidence_gates": {
    "minimum_independent_reviewers": 2,
    "require_positive_net_after_costs": true,
    "require_profit_factor_above_one": true,
    "require_shadow_validation": true,
    "require_no_critical_execution_faults": true
  },
  "live_auto_deploy": false,
  "draft_pr_only": true
}

Rules:
- Do not accept a recommendation merely because models agree; identify common hard evidence.
- Fewer than two successful reviewers means no normal strategy/code implementation candidate.
- A directly proven operational defect may be PAUSE_AND_FIX, never autonomous optimisation.
- CODE_CHANGE_CANDIDATE means a tested draft PR may later be prepared; it is not authority to merge/deploy.
- If evidence/sample is weak, choose NO_CHANGE or SHADOW_TEST.
- Preserve objective metrics and do not invent missing costs.

OBJECTIVE METRICS:
""" + json.dumps(_extract_objective_metrics(report), indent=2, sort_keys=True, default=str) + "\n\nINDEPENDENT REVIEWS:\n" + json.dumps(successful, indent=2, sort_keys=True, default=str)


def _validate_consensus(consensus: dict, successful_reviewers: int) -> None:
    if consensus.get("live_auto_deploy") is not False:
        raise RuntimeError("consensus failed live_auto_deploy=false safety gate")
    if consensus.get("draft_pr_only") is not True:
        raise RuntimeError("consensus failed draft_pr_only=true safety gate")
    if successful_reviewers < 2 and consensus.get("implementation_candidate") is True:
        if consensus.get("status") != "PAUSE_AND_FIX":
            raise RuntimeError("implementation candidate requires two independent reviewers")


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run(report_path: Path, output_dir: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "source_report.json", report)

    prompt = _review_prompt(report)
    reviews = [
        _provider_result(
            "openai", _openai_call, prompt,
            model=os.environ.get("OPENAI_REVIEW_MODEL", DEFAULT_OPENAI_REVIEW_MODEL).strip(),
            system=REVIEW_SYSTEM,
        ),
        _provider_result(
            "gemini", _gemini_call, prompt,
            model=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            system=REVIEW_SYSTEM,
        ),
        _provider_result(
            "anthropic", _anthropic_call, prompt,
            model=os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL).strip(),
            system=REVIEW_SYSTEM,
        ),
    ]
    for review in reviews:
        _write(output_dir / f"review_{review['provider']}.json", review)

    successful = sum(bool(r.get("ok")) for r in reviews)
    if successful and os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            consensus, meta = _openai_call(
                _synthesis_prompt(report, reviews),
                model=os.environ.get("OPENAI_SYNTH_MODEL", DEFAULT_OPENAI_SYNTH_MODEL).strip(),
                system=SYNTH_SYSTEM,
                timeout=150,
            )
            _validate_consensus(consensus, successful)
            consensus_result = {"ok": True, "consensus": consensus, "meta": meta}
        except Exception as exc:
            consensus_result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback": {
                    "status": "NO_CHANGE",
                    "implementation_candidate": False,
                    "live_auto_deploy": False,
                    "draft_pr_only": True,
                    "reason": "synthesis failed; no change permitted",
                },
            }
    else:
        consensus_result = {
            "ok": False,
            "error": "no synthesis available",
            "fallback": {
                "status": "NO_CHANGE",
                "implementation_candidate": False,
                "live_auto_deploy": False,
                "draft_pr_only": True,
                "reason": "insufficient independent review coverage",
            },
        }
    _write(output_dir / "consensus.json", consensus_result)

    summary = {
        "schema_version": 1,
        "generated_epoch": int(time.time()),
        "source_report": str(report_path),
        "source_report_sha256": _sha256(report),
        "successful_reviewers": successful,
        "providers": [
            {
                "provider": r["provider"],
                "ok": bool(r.get("ok")),
                "model": (r.get("meta") or {}).get("model"),
                "error": r.get("error"),
            }
            for r in reviews
        ],
        "consensus_ok": bool(consensus_result.get("ok")),
        "mode": "RESEARCH_AND_DRAFT_PR_ONLY",
        "live_auto_deploy": False,
    }
    _write(output_dir / "run_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.report.exists():
        print(f"report not found: {args.report}", file=sys.stderr)
        return 2
    try:
        print(json.dumps(run(args.report, args.output_dir), indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"multi-AI review failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
