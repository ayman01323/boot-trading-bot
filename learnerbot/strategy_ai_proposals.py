from __future__ import annotations

from typing import Any

from .strategy_lab import register_strategy
from .strategy_lab_research import request_asset

REQUIRED_FIELDS = (
    "name",
    "family",
    "hypothesis",
    "market_regime",
    "entry_logic",
    "exit_logic",
    "data_required",
    "estimated_costs",
    "failure_modes",
    "shadow_test",
    "minimum_observation_windows",
    "minimum_trades",
    "falsification_conditions",
    "differentiation",
)

MAX_PROPOSALS_PER_REVIEW = 4
MAX_TEXT = 4000


def _text(value: Any, field: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"AI strategy proposal missing {field}")
    if len(value) > MAX_TEXT:
        raise ValueError(f"AI strategy proposal field too long: {field}")
    return value


def _list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"AI strategy proposal {field} must be a non-empty list")
    out = [_text(item, field) for item in value]
    if len(out) > 30:
        raise ValueError(f"AI strategy proposal has too many {field} entries")
    return out


def _optional_list(value: Any, field: str, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list):
        raise ValueError(f"AI strategy proposal {field} must be a list")
    out = [_text(item, field) for item in value]
    if len(out) > 30:
        raise ValueError(f"AI strategy proposal has too many {field} entries")
    return out


def _clean_forecast(value: Any) -> dict:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("AI strategy proposal forecast must be an object")
    allowed = {
        "target", "horizon", "features", "model_family", "trade_threshold",
        "calibration_metric", "validation_split", "abstain_rule", "expected_edge_output",
    }
    out = {}
    for key, raw in value.items():
        if key not in allowed:
            continue
        if key == "features":
            out[key] = _optional_list(raw, "forecast.features")
        else:
            out[key] = _text(raw, f"forecast.{key}")
    if out and out.get("target", "").lower() not in {
        "positive_net_edge_after_costs",
        "positive realised net edge after costs",
        "positive realized net edge after costs",
    }:
        raise ValueError("forecast target must be positive net edge after costs")
    return out


def _clean_asset_requests(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("asset_requests must be a list")
    out = []
    for row in value[:20]:
        if not isinstance(row, dict):
            raise ValueError("asset request must be an object")
        chain = _text(row.get("chain"), "asset_request.chain").upper()
        if chain not in {"SOLANA", "EVM"}:
            raise ValueError("asset request chain must be SOLANA or EVM")
        out.append({
            "chain": chain,
            "asset": _text(row.get("asset"), "asset_request.asset"),
            "symbol": str(row.get("symbol") or "").strip().upper()[:40],
            "reason": _text(row.get("reason"), "asset_request.reason"),
            "evidence": str(row.get("evidence") or "").strip()[:2000],
        })
    return out


def validate_ai_strategy_proposal(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValueError("AI strategy proposal must be an object")
    missing = [key for key in REQUIRED_FIELDS if key not in proposal]
    if missing:
        raise ValueError("AI strategy proposal missing fields: " + ", ".join(missing))

    windows = int(proposal.get("minimum_observation_windows") or 0)
    trades = int(proposal.get("minimum_trades") or 0)
    if windows < 3 or windows > 168:
        raise ValueError("minimum_observation_windows must be between 3 and 168")
    if trades < 5 or trades > 10000:
        raise ValueError("minimum_trades must be between 5 and 10000")

    chain_scope = [x.upper() for x in _optional_list(proposal.get("chain_scope"), "chain_scope", default=["SOLANA", "EVM"])]
    if not chain_scope or any(x not in {"SOLANA", "EVM"} for x in chain_scope):
        raise ValueError("chain_scope may contain only SOLANA and EVM")

    clean = {
        "name": _text(proposal.get("name"), "name"),
        "family": _text(proposal.get("family"), "family").upper(),
        "hypothesis": _text(proposal.get("hypothesis"), "hypothesis"),
        "market_regime": _text(proposal.get("market_regime"), "market_regime"),
        "entry_logic": _text(proposal.get("entry_logic"), "entry_logic"),
        "exit_logic": _text(proposal.get("exit_logic"), "exit_logic"),
        "data_required": _list(proposal.get("data_required"), "data_required"),
        "estimated_costs": _text(proposal.get("estimated_costs"), "estimated_costs"),
        "failure_modes": _list(proposal.get("failure_modes"), "failure_modes"),
        "shadow_test": _text(proposal.get("shadow_test"), "shadow_test"),
        "minimum_observation_windows": windows,
        "minimum_trades": trades,
        "falsification_conditions": _list(proposal.get("falsification_conditions"), "falsification_conditions"),
        "differentiation": _text(proposal.get("differentiation"), "differentiation"),
        "chain_scope": chain_scope,
        "research_plan": _optional_list(proposal.get("research_plan"), "research_plan"),
        "research_tools": _optional_list(proposal.get("research_tools"), "research_tools"),
        "forecast": _clean_forecast(proposal.get("forecast")),
        "asset_requests": _clean_asset_requests(proposal.get("asset_requests")),
    }

    # Strategy proposals are declarative hypotheses, never snippets to execute.
    executable_markers = (
        "```",
        "subprocess.",
        "os.system(",
        "eval(",
        "exec(",
        "curl ",
        "wget ",
        "private_key",
        "seed phrase",
        "mnemonic",
        "api_key",
        "live_auto_deploy",
    )
    joined = " ".join(
        str(v) if not isinstance(v, list) else " ".join(str(x) for x in v)
        for v in clean.values()
    ).lower()
    bad = [marker for marker in executable_markers if marker in joined]
    if bad:
        raise ValueError("AI proposal contains executable/operational material: " + ", ".join(bad))

    return clean


def register_ai_strategy_proposal(app, proposal: dict, *, provider: str) -> dict:
    clean = validate_ai_strategy_proposal(proposal)
    params = {
        key: clean[key]
        for key in (
            "market_regime",
            "entry_logic",
            "exit_logic",
            "data_required",
            "estimated_costs",
            "failure_modes",
            "shadow_test",
            "minimum_observation_windows",
            "minimum_trades",
            "falsification_conditions",
            "differentiation",
            "chain_scope",
            "research_plan",
            "research_tools",
            "forecast",
        )
    }
    params["chain_specific_cost_model_required"] = True
    params["cross_chain_live_inference_forbidden"] = True
    registered = register_strategy(
        app,
        name=clean["name"],
        family=clean["family"],
        source="AI_PROPOSED",
        hypothesis=clean["hypothesis"],
        params=params,
        proposed_by=f"ai:{str(provider or 'unknown').strip().lower()}",
    )
    queued = []
    for req in clean["asset_requests"]:
        queued.append(request_asset(
            app,
            chain=req["chain"],
            asset=req["asset"],
            symbol=req["symbol"],
            reason=req["reason"],
            evidence=req["evidence"],
            proposed_by=f"ai:{str(provider or 'unknown').strip().lower()}",
        ))
    registered["asset_requests_queued"] = queued
    return registered


def register_ai_strategy_payload(app, payload: dict, *, provider: str) -> dict:
    """Register a bounded set of AI ideas as SHADOW research hypotheses.

    Invalid proposals are recorded in the return value rather than blocking other valid
    ideas. Nothing in this function can promote a strategy to LIVE or auto-add assets.
    """
    ideas = payload.get("new_strategy_hypotheses") if isinstance(payload, dict) else None
    if ideas is None:
        ideas = payload.get("strategies") if isinstance(payload, dict) else None
    if not isinstance(ideas, list):
        return {"provider": provider, "registered": [], "rejected": ["no strategy proposal list supplied"]}

    registered = []
    rejected = []
    for i, proposal in enumerate(ideas[:MAX_PROPOSALS_PER_REVIEW]):
        try:
            registered.append(register_ai_strategy_proposal(app, proposal, provider=provider))
        except Exception as exc:
            rejected.append({"index": i, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "provider": provider,
        "registered": registered,
        "rejected": rejected,
        "truncated": max(0, len(ideas) - MAX_PROPOSALS_PER_REVIEW),
        "live_auto_promote": False,
        "asset_auto_add": False,
    }
