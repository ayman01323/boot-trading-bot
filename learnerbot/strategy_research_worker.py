from __future__ import annotations

"""Isolated read-only online research worker for Strategy Factory.

The worker may fetch only through the existing bounded external-research collector. It
has no app object, filesystem writer, repository writer, trading hook, wallet/signing
primitive, deployment primitive, or configuration mutation API. Its output is untrusted
evidence which must cross the trusted ingestion/validation boundary before reuse.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .strategy_external_research import collect_external_strategy_research

WORKER_IDENTITY = "STRATEGY_RESEARCH_READONLY"
WORKER_ROLE = "ONLINE_RESEARCH"
SCHEMA_VERSION = 1

_TIME_SENSITIVE = {
    "current", "latest", "recent", "today", "fee", "fees", "gas", "liquidity",
    "volume", "incident", "outage", "version", "upgrade", "protocol", "route",
    "latency", "rpc", "price", "market", "competitor", "exploit", "security",
}
_INTERNAL_ONLY = {
    "backtest", "our trades", "our pnl", "repository", "repo", "git sha",
    "internal", "strategy version", "historical run",
}
_STABLE_EXTERNAL = {
    "paper", "academic", "whitepaper", "specification", "algorithm", "methodology",
}

_SOURCE_TIERS = {
    "PRIMARY_RAW_DATA": 2,
    "OFFICIAL_API_WEBSOCKET": 1,
    "ACADEMIC_RESEARCH": 3,
    "ACADEMIC_PREPRINT_RESEARCH": 3,
    "OPEN_SOURCE_IDEA_RESEARCH": 4,
}

_TTL_SECONDS = {
    "FRESH_WEB": 6 * 60 * 60,
    "CACHE_FIRST": 30 * 24 * 60 * 60,
    "REPO_ONLY": 365 * 24 * 60 * 60,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def classify_research_question(question: str) -> dict:
    text = " ".join(str(question or "").lower().split())
    if any(token in text for token in _TIME_SENSITIVE):
        freshness_class = "FRESH_WEB"
        route = "WEB_REQUIRED"
    elif any(token in text for token in _INTERNAL_ONLY):
        freshness_class = "REPO_ONLY"
        route = "REPO_HISTORY_ONLY"
    elif any(token in text for token in _STABLE_EXTERNAL):
        freshness_class = "CACHE_FIRST"
        route = "CACHE_THEN_WEB"
    else:
        freshness_class = "CACHE_FIRST"
        route = "CACHE_THEN_WEB"
    return {
        "freshness_class": freshness_class,
        "route": route,
        "ttl_seconds": _TTL_SECONDS[freshness_class],
    }


def _https(url: Any) -> bool:
    try:
        p = urlparse(str(url or "").strip())
        return p.scheme == "https" and bool(p.netloc) and not p.username and not p.password
    except Exception:
        return False


def _source_tier(source: dict) -> int:
    source_class = str(source.get("source_class") or "").upper().strip()
    if source_class in _SOURCE_TIERS:
        return _SOURCE_TIERS[source_class]
    source_id = str(source.get("source_id") or "").upper().strip()
    if source_id in {"EXT1", "EXT4"}:
        return 2
    if source_id == "EXT3":
        return 3
    return 4


def _published_date(source: dict) -> str | None:
    data = source.get("data") if isinstance(source.get("data"), dict) else {}
    papers = data.get("papers") if isinstance(data.get("papers"), list) else []
    if papers and isinstance(papers[0], dict):
        value = str(papers[0].get("published") or papers[0].get("updated") or "").strip()
        return value or None
    repos = data.get("repositories") if isinstance(data.get("repositories"), list) else []
    if repos and isinstance(repos[0], dict):
        value = str(repos[0].get("updated_at") or "").strip()
        return value or None
    return None


def _supporting_excerpt(source: dict, *, limit: int = 700) -> str:
    # This is deliberately a bounded evidence snippet, not a copied webpage/article.
    raw = json.dumps(source.get("data"), sort_keys=True, separators=(",", ":"), default=str)
    return raw[: max(0, int(limit))]


def _finding(source: dict, *, hypothesis_id: str, now: int, ttl_seconds: int) -> dict:
    url = str(source.get("canonical_url") or "").strip()
    if not _https(url):
        raise ValueError("research source requires canonical HTTPS URL")
    tier = _source_tier(source)
    accessed = str(source.get("retrieved_utc") or datetime.now(timezone.utc).isoformat())
    notes = str(source.get("notes") or "Fresh external research snapshot.").strip()
    return {
        "source_id": str(source.get("source_id") or "").strip(),
        "hypothesis_id": str(hypothesis_id or "").strip(),
        "claim_kind": "SOURCE_SNAPSHOT",
        "claim": notes[:1000],
        "supporting_excerpt": _supporting_excerpt(source),
        "url": url,
        "source_tier": tier,
        "publish_date": _published_date(source),
        "access_date_utc": accessed,
        "retrieved_epoch": now,
        "ttl_expiry_epoch": now + int(ttl_seconds),
        "confidence": 0.90 if tier <= 2 else 0.80 if tier == 3 else 0.65,
        "disputed": False,
        "core_assumption": False,
        "retrieved_by": WORKER_IDENTITY,
        "instruction_authority": False,
        "research_only": True,
        "content_sha256": str(source.get("data_sha256") or _sha256(source.get("data"))),
        "corroborating_sources": [],
    }


def build_worker_payload(
    *,
    question: str,
    hypothesis_id: str,
    external_pack: dict | None,
    now: int | None = None,
) -> dict:
    now = int(now or time.time())
    routing = classify_research_question(question)
    findings = []
    errors = []
    if external_pack:
        for source in external_pack.get("sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                findings.append(
                    _finding(
                        source,
                        hypothesis_id=hypothesis_id,
                        now=now,
                        ttl_seconds=int(routing["ttl_seconds"]),
                    )
                )
            except Exception as exc:
                errors.append({"source_id": source.get("source_id"), "error": f"{type(exc).__name__}: {exc}"})
        errors.extend(list(external_pack.get("errors") or []))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "worker_identity": WORKER_IDENTITY,
        "worker_role": WORKER_ROLE,
        "hypothesis_id": str(hypothesis_id or "").strip(),
        "question": str(question or "").strip(),
        "question_sha256": _sha256(str(question or "").strip()),
        "generated_epoch": now,
        "generated_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "freshness_class": routing["freshness_class"],
        "route": routing["route"],
        "ttl_seconds": int(routing["ttl_seconds"]),
        "cache_expires_epoch": now + int(routing["ttl_seconds"]),
        "findings": findings,
        "errors": errors,
        "raw_evidence_sha256": str((external_pack or {}).get("evidence_sha256") or ""),
        "research_only": True,
        "write_authority": False,
        "repo_write_authority": False,
        "config_write_authority": False,
        "trading_authority": False,
        "live_execution_authorised": False,
        "wallet_signing_authority": False,
        "external_content_instruction_authority": False,
        "challenge_status": "NOT_RUN",
    }
    payload["payload_sha256"] = _sha256({k: v for k, v in payload.items() if k != "payload_sha256"})
    return payload


def run_research_worker(
    *,
    question: str,
    hypothesis_id: str,
    github_token: str | None = None,
    session=None,
    now: int | None = None,
) -> dict:
    """Execute one no-write research turn and return inert structured evidence."""
    routing = classify_research_question(question)
    pack = None
    if routing["route"] != "REPO_HISTORY_ONLY":
        pack = collect_external_strategy_research(github_token=github_token, session=session)
    return build_worker_payload(question=question, hypothesis_id=hypothesis_id, external_pack=pack, now=now)
