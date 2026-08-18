from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6"
MAX_DECISION_ROWS = 300
MAX_ERROR_ROWS = 100

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["HEALTHY", "WATCH", "DEGRADED"]},
        "executive_summary": {"type": "string"},
        "findings": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    "category": {"type": "string", "enum": ["EXECUTION", "LATENCY", "ACCOUNTING", "DATA_QUALITY", "SELECTION_RESEARCH", "RISK_CONTROL", "INFRASTRUCTURE"]},
                    "evidence": {"type": "string"},
                    "interpretation": {"type": "string"},
                    "shadow_test": {"type": "string"},
                },
                "required": ["severity", "category", "evidence", "interpretation", "shadow_test"],
            },
        },
        "shadow_candidate": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["SHADOW_ONLY"]},
                "hypothesis": {"type": "string"},
                "experiments": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                            "change_description": {"type": "string"},
                            "success_metric": {"type": "string"},
                            "minimum_observation_hours": {"type": "integer", "minimum": 1, "maximum": 168},
                        },
                        "required": ["name", "change_description", "success_metric", "minimum_observation_hours"],
                    },
                },
                "live_promotion_requires_human_approval": {"type": "boolean", "enum": [True]},
            },
            "required": ["mode", "hypothesis", "experiments", "live_promotion_requires_human_approval"],
        },
        "recommended_action": {"type": "string", "enum": ["KEEP_CURRENT_LIVE_SETTINGS", "PAUSE_AND_INVESTIGATE", "RUN_SHADOW_EXPERIMENTS"]},
        "do_not_auto_deploy_live": {"type": "boolean", "enum": [True]},
    },
    "required": ["status", "executive_summary", "findings", "shadow_candidate", "recommended_action", "do_not_auto_deploy_live"],
}


def _utc(epoch: int | None = None) -> str:
    if epoch is None:
        epoch = int(time.time())
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _anon(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "unknown"
    return "user_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _d(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _csv_from_zip(zf: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        raw = zf.read(name).decode("utf-8-sig")
    except KeyError:
        return []
    return list(csv.DictReader(raw.splitlines()))


def _json_from_zip(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        return json.loads(zf.read(name).decode("utf-8"))
    except Exception:
        return {}


def _decision_digest(zf: zipfile.ZipFile) -> dict:
    counts = Counter()
    reasons = Counter()
    sampled = 0
    for name in zf.namelist():
        low = name.lower()
        if not name.startswith("bot_db/") or not low.endswith(".csv"):
            continue
        if not any(part in low for part in ("decision", "execution", "attempt", "position", "trade")):
            continue
        try:
            rows = _csv_from_zip(zf, name)
        except Exception:
            continue
        for row in rows[:MAX_DECISION_ROWS]:
            sampled += 1
            status = str(row.get("decision") or row.get("status") or row.get("action") or "").upper().strip()
            if status:
                counts[status] += 1
            reason = str(row.get("reason") or row.get("note") or row.get("rejection_reason") or row.get("error") or "").strip()
            if reason:
                reasons[reason[:180]] += 1
    return {
        "rows_sampled": sampled,
        "status_counts": dict(counts.most_common(30)),
        "top_reasons": [{"reason": k, "count": v} for k, v in reasons.most_common(20)],
    }


def build_review_metrics(zip_path: str | Path) -> dict:
    path = Path(zip_path)
    with zipfile.ZipFile(path) as zf:
        summary = _json_from_zip(zf, "summary.json")
        txs = _csv_from_zip(zf, "all_transactions.csv")
        errors = _csv_from_zip(zf, "collection_errors.csv")[:MAX_ERROR_ROWS]
        decisions = _decision_digest(zf)

    chain_counts = Counter()
    action_counts = Counter()
    status_counts = Counter()
    user_counts = Counter()
    source_counts = Counter()
    fee_by_chain = defaultdict(Decimal)
    native_delta_by_chain = defaultdict(Decimal)
    failed_by_chain = Counter()

    for row in txs:
        chain = str(row.get("chain_slug") or "unknown")
        action = str(row.get("action") or "unknown").upper()
        status = str(row.get("status") or "unknown").upper()
        source = str(row.get("source") or "unknown")
        user = _anon(row.get("telegram_id") or "")
        chain_counts[chain] += 1
        action_counts[action] += 1
        status_counts[status] += 1
        source_counts[source] += 1
        user_counts[user] += 1
        fee_by_chain[chain] += _d(row.get("fee_native"))
        native_delta_by_chain[chain] += _d(row.get("native_delta"))
        if status == "FAILED":
            failed_by_chain[chain] += 1

    error_digest = Counter()
    for row in errors:
        key = "%s/%s: %s" % (
            str(row.get("chain") or "unknown"),
            str(row.get("stage") or "unknown"),
            str(row.get("error") or "")[:180],
        )
        error_digest[key] += 1

    return {
        "generated_utc": _utc(),
        "audit_summary": {
            "requested_hours": summary.get("requested_hours"),
            "window_start_utc": summary.get("window_start_utc"),
            "registered_users": summary.get("registered_users"),
            "enabled_wallets": summary.get("enabled_wallets"),
            "collection_errors": summary.get("collection_errors"),
            "cumulative_rows": summary.get("cumulative_rows"),
        },
        "transaction_rows": len(txs),
        "chain_counts": dict(chain_counts),
        "action_counts": dict(action_counts),
        "status_counts": dict(status_counts),
        "source_counts": dict(source_counts),
        "per_anonymous_user_rows": dict(user_counts),
        "fee_native_by_chain": {k: format(v, "f") for k, v in fee_by_chain.items()},
        "native_delta_by_chain": {k: format(v, "f") for k, v in native_delta_by_chain.items()},
        "failed_transactions_by_chain": dict(failed_by_chain),
        "bot_decision_digest": decisions,
        "collection_error_digest": [{"error": k, "count": v} for k, v in error_digest.most_common(20)],
        "privacy_note": "Telegram IDs are hashed before being sent to OpenAI; wallet addresses and private key material are not included in this review payload.",
    }


def _extract_output_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    raise RuntimeError("OpenAI response did not contain output text")


def _validate_review(review: dict) -> None:
    if not isinstance(review, dict):
        raise RuntimeError("GPT review is not a JSON object")
    if review.get("do_not_auto_deploy_live") is not True:
        raise RuntimeError("GPT review failed live-deploy safety gate")
    candidate = review.get("shadow_candidate") or {}
    if candidate.get("mode") != "SHADOW_ONLY":
        raise RuntimeError("GPT review candidate is not SHADOW_ONLY")
    if candidate.get("live_promotion_requires_human_approval") is not True:
        raise RuntimeError("GPT review omitted human approval gate")


def request_gpt_review(metrics: dict, *, api_key: str | None = None, model: str | None = None, timeout: int = 90) -> tuple[dict, dict]:
    key = str(api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    selected_model = str(model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    instructions = (
        "You are reviewing an automated multi-chain trading bot's hourly operational audit. "
        "Analyse execution quality, latency, accounting/data integrity, infrastructure reliability, and research quality. "
        "Do not recommend buying or selling any particular asset. Do not produce live trading instructions. "
        "Any proposed experiment must be SHADOW_ONLY and must require explicit human approval before live promotion. "
        "Use only the supplied evidence; distinguish observed facts from hypotheses. "
        "If the data is insufficient, say so. Prioritise preventing repeated execution losses and corrupted P&L over increasing trade frequency."
    )
    body = {
        "model": selected_model,
        "store": False,
        "instructions": instructions,
        "input": json.dumps(metrics, separators=(",", ":"), default=str),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hourly_trading_audit_review",
                "description": "Operational trading-bot review with shadow-only experiments and a mandatory human live-deploy gate.",
                "schema": REVIEW_SCHEMA,
                "strict": True,
            }
        },
    }
    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    text = _extract_output_text(payload)
    review = json.loads(text)
    _validate_review(review)
    meta = {
        "response_id": payload.get("id"),
        "model": payload.get("model") or selected_model,
        "status": payload.get("status"),
        "usage": payload.get("usage") or {},
    }
    return review, meta


def run_hourly_gpt_review(app, zip_path: str | Path) -> dict:
    metrics = build_review_metrics(zip_path)
    root = Path(app.data_dir) / "transaction_audits" / "gpt_reviews"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    result = {
        "generated_utc": _utc(),
        "source_zip": str(zip_path),
        "mode": "SHADOW_ONLY",
        "live_auto_deploy": False,
        "metrics": metrics,
    }
    try:
        review, api_meta = request_gpt_review(metrics)
        result.update({"ok": True, "review": review, "openai": api_meta})
    except Exception as exc:
        result.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    path = root / f"hourly_gpt_review_{stamp}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    latest = root / "latest_gpt_review.json"
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(tmp, latest)
    result["report_path"] = str(path)
    result["latest_report"] = str(latest)
    return result
