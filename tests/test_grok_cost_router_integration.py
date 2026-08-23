from __future__ import annotations

from learnerbot import ai_cost_grok_patch as _grok_cost  # noqa: F401
from learnerbot import ai_cost_router as cost
from learnerbot import ai_cost_provider_patch as provider_patch
from learnerbot import master_change_cost_router_patch as master_cost
from scripts import ai_agent_ws_bus_grok
from scripts import ai_agent_ws_worker
from scripts import ai_mailbox_provider_relay
from scripts import master_change_policy


def test_cheap_routes_remain_unchanged_and_full_council_adds_grok() -> None:
    assert cost.route_request("improve documentation wording")["advisers"] == ["deepseek"]
    assert cost.route_request("fix the parser bug in Telegram")["advisers"] == ["deepseek", "gemini"]
    assert cost.route_request("change websocket architecture and service queue")["advisers"] == ["gemini", "claude"]
    critical = cost.route_request("deploy new live trade execution risk logic")
    assert critical["level"] == 4
    assert "grok" in critical["advisers"]
    assert critical["advisers"] == list(cost.ALL_ADVISERS)
    assert critical["model_calls_before_implementation"] == 6


def test_grok_models_have_explicit_cost_rates() -> None:
    assert cost._rate("grok", "grok-build-0.1") == (1.0, 2.0, 0.20)
    assert cost._rate("grok", "grok-4.20-non-reasoning") == (1.25, 2.50, 0.20)
    assert cost._rate("grok", "grok-4.6") == (2.0, 6.0, 0.50)
    assert provider_patch._model("grok") == "grok-4.20-non-reasoning"


def test_xai_usage_is_parsed_for_cost_accounting() -> None:
    usage = cost.usage_from_response(
        "grok",
        {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 40},
            }
        },
    )
    assert usage == {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 30}


def test_grok_is_a_persistent_bus_recipient() -> None:
    assert "grok" in ai_agent_ws_bus_grok.AGENTS
    assert "grok" in ai_agent_ws_worker.AGENTS
    assert ai_agent_ws_worker.low_cost_model("grok") == "grok-4.20-non-reasoning"


def test_grok_is_supported_by_bounded_mailbox_and_master_change_council() -> None:
    assert "grok" in ai_mailbox_provider_relay._ALLOWED_PROVIDERS
    assert "XAI_API_KEY" in ai_mailbox_provider_relay._SECRET_ENV_KEYS
    assert "grok" in master_cost._base.ADVISERS
    assert "grok" in master_change_policy.ALL_ADVISERS


def test_master_policy_requires_grok_on_level_four() -> None:
    request = "change live trading execution risk limit"
    route = cost.master_change_route(request, protected_reasons=["live", "risk limit"])
    required = list(route["advisers"])
    assert "grok" in required
    evidence = {
        "schema_version": 2,
        "request_id": "mc-20260823T100000Z-grok01",
        "request": request,
        "implementation_nonce": 1,
        "implementation_allowed": True,
        "hard_protected_reasons": [],
        "protected_reasons": ["live", "risk limit"],
        "all_advisers_replied": True,
        "source_sha": "a" * 40,
        "cost_route": route,
        "required_advisers": required,
        "advisers": {
            name: {"acknowledged": True, "provider_rc": 0, "reply": "APPROVE"}
            for name in required
        },
        "gpt_decision": {
            "action": "IMPLEMENT",
            "risk_class": "HIGH",
            "allowed_files": ["learnerbot/non_governance_example.py"],
            "auto_merge_recommended": False,
        },
    }
    del evidence["advisers"]["grok"]
    try:
        master_change_policy.validate_request(
            evidence,
            request_id=evidence["request_id"],
            nonce=1,
            current_sha="a" * 40,
        )
    except ValueError as exc:
        assert "grok required adviser" in str(exc)
    else:
        raise AssertionError("Level 4 policy accepted evidence without Grok")
