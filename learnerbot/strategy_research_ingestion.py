from __future__ import annotations

"""Trusted ingestion/cache boundary for Strategy Factory online research.

Only this layer writes research evidence. The research worker itself is deliberately
stateless/no-write. Stored evidence is inert and cannot alter LIVE, capital, risk,
configuration, wallets/signing, or deployment state.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .strategy_research_worker import WORKER_IDENTITY

STORE_SCHEMA_VERSION = 1
_STORE_NAME = "strategy_research/findings.json"
_CAPITAL_STAGES = {"CANARY", "LIVE", "FULL_LIVE", "ACTIVE"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _https(value: Any) -> bool:
    try:
        p = urlparse(str(value or "").strip())
        return p.scheme == "https" and bool(p.netloc) and not p.username and not p.password
    except Exception:
        return False


def _store_path(app) -> Path:
    return Path(app.data_dir) / _STORE_NAME


def validate_worker_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("research worker payload must be an object")
    if str(payload.get("worker_identity") or "") != WORKER_IDENTITY:
        raise ValueError("unexpected research worker identity")
    if not str(payload.get("hypothesis_id") or "").strip():
        raise ValueError("hypothesis_id is required")
    if not str(payload.get("question") or "").strip():
        raise ValueError("research question is required")
    if payload.get("research_only") is not True:
        raise ValueError("research payload must be research_only")
    forbidden_authority = (
        "write_authority",
        "repo_write_authority",
        "config_write_authority",
        "trading_authority",
        "live_execution_authorised",
        "wallet_signing_authority",
        "external_content_instruction_authority",
    )
    for key in forbidden_authority:
        if payload.get(key) is not False:
            raise ValueError(f"research worker must have {key}=false")
    generated = int(payload.get("generated_epoch") or 0)
    expires = int(payload.get("cache_expires_epoch") or 0)
    if generated <= 0 or expires <= generated:
        raise ValueError("research cache timestamps are invalid")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"findings[{idx}] must be an object")
        for key in ("source_id", "hypothesis_id", "claim", "url", "retrieved_by", "content_sha256"):
            if not str(finding.get(key) or "").strip():
                raise ValueError(f"findings[{idx}] missing {key}")
        if str(finding.get("hypothesis_id")) != str(payload.get("hypothesis_id")):
            raise ValueError(f"findings[{idx}] hypothesis_id mismatch")
        if str(finding.get("retrieved_by")) != WORKER_IDENTITY:
            raise ValueError(f"findings[{idx}] retrieved_by mismatch")
        if not _https(finding.get("url")):
            raise ValueError(f"findings[{idx}] requires canonical HTTPS url")
        tier = int(finding.get("source_tier") or 0)
        if tier not in {1, 2, 3, 4, 5}:
            raise ValueError(f"findings[{idx}] source_tier must be 1..5")
        if finding.get("instruction_authority") is not False or finding.get("research_only") is not True:
            raise ValueError(f"findings[{idx}] must remain inert research evidence")
        if int(finding.get("ttl_expiry_epoch") or 0) <= int(finding.get("retrieved_epoch") or 0):
            raise ValueError(f"findings[{idx}] TTL is invalid")
        excerpt = str(finding.get("supporting_excerpt") or "")
        if len(excerpt) > 1200:
            raise ValueError(f"findings[{idx}] supporting_excerpt is too long")
    return payload


def _read_store(app) -> dict:
    path = _store_path(app)
    if not path.exists():
        return {"schema_version": STORE_SCHEMA_VERSION, "entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": STORE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
        return {"schema_version": STORE_SCHEMA_VERSION, "entries": {}}
    return raw


def _entry_key(hypothesis_id: str, question: str) -> str:
    return _sha256({"hypothesis_id": str(hypothesis_id), "question": str(question)})


def ingest_research_payload(app, payload: dict) -> dict:
    """Validate and atomically persist inert research evidence in the trusted layer."""
    validate_worker_payload(payload)
    store = _read_store(app)
    key = _entry_key(str(payload["hypothesis_id"]), str(payload["question"]))
    entries = dict(store.get("entries") or {})
    entries[key] = dict(payload)
    # Bound retention. The newest payloads are retained by generated_epoch.
    if len(entries) > 500:
        ordered = sorted(entries.items(), key=lambda kv: int((kv[1] or {}).get("generated_epoch") or 0), reverse=True)
        entries = dict(ordered[:500])
    out = {
        "schema_version": STORE_SCHEMA_VERSION,
        "updated_epoch": int(time.time()),
        "entries": entries,
        "research_only": True,
        "live_execution_authorised": False,
    }
    path = _store_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"stored": True, "entry_key": key, "path": str(path), "payload_sha256": payload.get("payload_sha256")}


def load_cached_payload(app, *, hypothesis_id: str, question: str, now: int | None = None) -> dict | None:
    now = int(now or time.time())
    store = _read_store(app)
    payload = (store.get("entries") or {}).get(_entry_key(hypothesis_id, question))
    if not isinstance(payload, dict):
        return None
    try:
        validate_worker_payload(payload)
    except Exception:
        return None
    if int(payload.get("cache_expires_epoch") or 0) <= now:
        return None
    return payload


def research_freshness_summary(payload: dict | None, *, now: int | None = None) -> dict:
    now = int(now or time.time())
    if not isinstance(payload, dict):
        return {
            "available": False,
            "current_findings": 0,
            "expired_findings": 0,
            "disputed_findings": 0,
            "tier_1_2_current": 0,
            "challenge_status": "NOT_RUN",
        }
    current = []
    expired = []
    disputed = []
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if bool(finding.get("disputed")):
            disputed.append(finding)
        if int(finding.get("ttl_expiry_epoch") or 0) > now:
            current.append(finding)
        else:
            expired.append(finding)
    return {
        "available": True,
        "current_findings": len(current),
        "expired_findings": len(expired),
        "disputed_findings": len(disputed),
        "tier_1_2_current": sum(1 for row in current if int(row.get("source_tier") or 9) <= 2),
        "challenge_status": str(payload.get("challenge_status") or "NOT_RUN").upper(),
        "cache_expires_epoch": int(payload.get("cache_expires_epoch") or 0),
        "freshness_class": str(payload.get("freshness_class") or ""),
    }


def promotion_research_gate(payload: dict | None, *, target_stage: str, now: int | None = None) -> dict:
    """Fail closed at capital-risk stages without fresh, non-disputed research evidence.

    SHADOW/EXPERIMENT remain exploration lanes. CANARY requires current evidence and at
    least one current tier-1/2 source. FULL LIVE additionally requires an independent
    challenge marked PASS. This function never promotes anything; it only returns gate
    eligibility for a caller to combine with all existing execution/safety gates.
    """
    stage = str(target_stage or "").upper().strip()
    summary = research_freshness_summary(payload, now=now)
    reasons = []
    if stage not in _CAPITAL_STAGES:
        return {"eligible": True, "target_stage": stage, "reasons": ["research advisory at non-capital stage"], "summary": summary}
    if not summary["available"]:
        reasons.append("no validated research payload")
    else:
        if summary["current_findings"] <= 0:
            reasons.append("no current research findings")
        if summary["expired_findings"] > 0:
            reasons.append("one or more findings expired")
        if summary["disputed_findings"] > 0:
            reasons.append("one or more findings disputed")
        if summary["tier_1_2_current"] <= 0:
            reasons.append("no current tier-1/2 corroboration")
        if stage in {"LIVE", "FULL_LIVE", "ACTIVE"} and summary["challenge_status"] != "PASS":
            reasons.append("independent challenge has not passed")
    return {
        "eligible": not reasons,
        "target_stage": stage,
        "reasons": reasons or ["research freshness/provenance gate satisfied"],
        "summary": summary,
        "does_not_authorise_live": True,
    }


def record_challenge_result(app, *, hypothesis_id: str, question: str, status: str, challenger: str, now: int | None = None) -> dict:
    """Trusted-layer update of challenge metadata only; no strategy/runtime mutation."""
    status = str(status or "").upper().strip()
    if status not in {"PASS", "FAIL", "DISPUTED"}:
        raise ValueError("challenge status must be PASS, FAIL or DISPUTED")
    store = _read_store(app)
    key = _entry_key(hypothesis_id, question)
    payload = (store.get("entries") or {}).get(key)
    if not isinstance(payload, dict):
        raise ValueError("research payload not found")
    validate_worker_payload(payload)
    payload = dict(payload)
    payload["challenge_status"] = status
    payload["challenger"] = str(challenger or "").strip()[:120]
    payload["challenged_epoch"] = int(now or time.time())
    if status in {"FAIL", "DISPUTED"}:
        payload["findings"] = [dict(row, disputed=True) if isinstance(row, dict) else row for row in payload.get("findings") or []]
    # Recompute no authority fields explicitly before storage.
    payload["research_only"] = True
    payload["live_execution_authorised"] = False
    payload["payload_sha256"] = _sha256({k: v for k, v in payload.items() if k != "payload_sha256"})
    store["entries"][key] = payload
    path = _store_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"recorded": True, "status": status, "entry_key": key}
