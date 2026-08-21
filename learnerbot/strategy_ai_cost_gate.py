from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

DEFAULT_FORCE_REFRESH_SECONDS = 6 * 60 * 60
_NATIVE_QUANTUM = Decimal("0.001")
_PF_QUANTUM = Decimal("0.01")


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _quantized(value: Any, quantum: Decimal) -> str:
    return format(_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _bucket_counts(value: Any, size: int) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(value, dict):
        return out
    step = max(1, int(size))
    for key, raw in sorted(value.items(), key=lambda item: str(item[0])):
        try:
            count = int(raw or 0)
        except (TypeError, ValueError):
            continue
        out[str(key)] = count // step
    return out


def _money_map(value: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(value, dict):
        return out
    for key, raw in sorted(value.items(), key=lambda item: str(item[0])):
        out[str(key)] = _quantized(raw, _NATIVE_QUANTUM)
    return out


def _error_digest(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value[:20]:
        if not isinstance(row, dict):
            continue
        rows.append({
            "error": str(row.get("error") or "")[:180],
            "count": int(row.get("count") or 0),
        })
    return rows


def _stable_profit_control_state(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    keep: dict[str, str] = {}
    for key, raw in sorted(value.items(), key=lambda item: str(item[0])):
        name = str(key).lower()
        if not any(token in name for token in ("profile", "mode", "status", "circuit", "pause")):
            continue
        if any(token in name for token in ("time", "epoch", "updated", "generated", "last_run")):
            continue
        keep[str(key)] = str(raw)
    return keep


def _strategy_registry(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        rows.append({
            "profile": str(row.get("profile") or ""),
            "closed_trades": int(row.get("closed_trades") or 0),
            "wins": int(row.get("wins") or 0),
            "losses": int(row.get("losses") or 0),
            "net_sol": _quantized(row.get("net_sol"), _NATIVE_QUANTUM),
            "profit_factor": _quantized(row.get("profit_factor"), _PF_QUANTUM),
            "successful": bool(int(row.get("successful") or 0)) if str(row.get("successful") or "").strip().isdigit() else bool(row.get("successful")),
        })
    return sorted(rows, key=lambda row: row["profile"])


def build_material_snapshot(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return only material, low-churn Strategy Lab evidence for paid-AI gating.

    High-frequency timestamps, raw transaction rows and live mark-to-market values are
    intentionally excluded. Realised P&L, failures, strategy/profile state and coarse
    EVM activity/economic changes remain in the snapshot.
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    audit = evidence.get("audit_metrics") if isinstance(evidence.get("audit_metrics"), dict) else {}
    decisions = audit.get("bot_decision_digest") if isinstance(audit.get("bot_decision_digest"), dict) else {}
    solana = evidence.get("solana_live") if isinstance(evidence.get("solana_live"), dict) else {}
    perf = solana.get("performance") if isinstance(solana.get("performance"), dict) else {}
    control = evidence.get("profit_control") if isinstance(evidence.get("profit_control"), dict) else {}

    return {
        "schema_version": int(evidence.get("schema_version") or 0),
        "evidence_status": str(evidence.get("evidence_status") or "AVAILABLE"),
        "window_hours": int(evidence.get("window_hours") or 0),
        "audit": {
            # Coarse buckets avoid paying three models for every ordinary transaction.
            "chain_activity_buckets_25": _bucket_counts(audit.get("chain_counts"), 25),
            "action_buckets_10": _bucket_counts(audit.get("action_counts"), 10),
            "status_buckets_5": _bucket_counts(audit.get("status_counts"), 5),
            "failed_transactions_by_chain": _bucket_counts(audit.get("failed_transactions_by_chain"), 1),
            "native_delta_by_chain_q001": _money_map(audit.get("native_delta_by_chain")),
            "fee_native_by_chain_q001": _money_map(audit.get("fee_native_by_chain")),
            "decision_status_buckets_10": _bucket_counts(decisions.get("status_counts"), 10),
            "collection_error_digest": _error_digest(audit.get("collection_error_digest")),
        },
        "solana_realised": {
            "closed_trades": int(perf.get("closed_trades") or 0),
            "wins": int(perf.get("wins") or 0),
            "losses": int(perf.get("losses") or 0),
            "gross_profit_sol": _quantized(perf.get("gross_profit_sol"), _NATIVE_QUANTUM),
            "gross_loss_sol": _quantized(perf.get("gross_loss_sol"), _NATIVE_QUANTUM),
            "net_sol": _quantized(perf.get("net_sol"), _NATIVE_QUANTUM),
            "profit_factor": _quantized(perf.get("profit_factor"), _PF_QUANTUM),
            "exit_reason_counts": _bucket_counts(perf.get("exit_reason_counts"), 1),
            "exit_circuit_status_counts": _bucket_counts(solana.get("exit_circuit_status_counts"), 1),
        },
        "profit_control": {
            "state": _stable_profit_control_state(control.get("state")),
            "strategy_registry": _strategy_registry(control.get("strategy_registry")),
        },
    }


def material_sha256(evidence: dict[str, Any]) -> str:
    payload = build_material_snapshot(evidence)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_cost_gate(
    *,
    source_commit: str,
    evidence: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    manual: bool = False,
    now_epoch: int | None = None,
    force_refresh_seconds: int = DEFAULT_FORCE_REFRESH_SECONDS,
) -> dict[str, Any]:
    previous = previous_state if isinstance(previous_state, dict) else {}
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    force_after = max(3600, int(force_refresh_seconds or DEFAULT_FORCE_REFRESH_SECONDS))
    current_hash = material_sha256(evidence)
    last_epoch = int(previous.get("last_ai_attempt_epoch") or 0)
    last_source = str(previous.get("last_ai_attempt_source_commit") or "")
    last_hash = str(previous.get("last_ai_attempt_material_sha256") or "")
    age = max(0, now - last_epoch) if last_epoch else None

    if manual:
        run, reason = True, "MANUAL_REQUEST"
    elif not last_epoch or not last_source or not last_hash:
        run, reason = True, "NO_PREVIOUS_AI_ATTEMPT"
    elif str(source_commit) != last_source:
        run, reason = True, "SOURCE_COMMIT_CHANGED"
    elif current_hash != last_hash:
        run, reason = True, "MATERIAL_EVIDENCE_CHANGED"
    elif age is not None and age >= force_after:
        run, reason = True, "FORCED_REFRESH_DUE"
    else:
        run, reason = False, "UNCHANGED_WITHIN_REFRESH_WINDOW"

    return {
        "run_ai": bool(run),
        "reason": reason,
        "material_sha256": current_hash,
        "material_snapshot": build_material_snapshot(evidence),
        "source_commit": str(source_commit),
        "checked_epoch": now,
        "seconds_since_last_ai_attempt": age,
        "force_refresh_seconds": force_after,
    }
