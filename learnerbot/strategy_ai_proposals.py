from __future__ import annotations

from typing import Any

from .strategy_lab import register_strategy

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
        str(v) if not isinstance(v, list) else " ".join(v)
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
        )
    }
    return register_strategy(
        app,
        name=clean["name"],
        family=clean["family"],
        source="AI_PROPOSED",
        hypothesis=clean["hypothesis"],
        params=params,
        proposed_by=f"ai:{str(provider or 'unknown').strip().lower()}",
    )


def register_ai_strategy_payload(app, payload: dict, *, provider: str) -> dict:
    """Register a bounded set of AI ideas as SHADOW research hypotheses.

    Invalid proposals are recorded in the return value rather than blocking other valid
    ideas. Nothing in this function can promote a strategy to LIVE.
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
    }
