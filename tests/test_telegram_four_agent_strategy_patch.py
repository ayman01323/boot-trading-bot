from __future__ import annotations

from learnerbot import telegram_four_agent_strategy_patch as patch


def _state(*, gpt="DONE", gemini="DONE", copilot="DONE", claude="WAITING", cycle="cycle-1"):
    return {
        "engineering": {},
        "strategy": {
            "available": True,
            "cycle_id": cycle,
            "gpt": gpt,
            "gemini": gemini,
            "copilot": copilot,
            "claude": claude,
            "three_agent_reports_complete": all(x == "DONE" for x in (gpt, gemini, copilot)),
            "four_agent_reports_complete": all(x == "DONE" for x in (gpt, gemini, copilot, claude)),
            "master_decision_available": False,
            "decision_counts": {"ACCEPT": 0, "REJECT": 0, "DEFER": 0},
            "change_pr_url": "",
        },
    }


def test_start_notification_names_all_four_agents():
    previous = {"engineering": {}, "strategy": {"available": False}}
    current = _state(claude="WAITING")
    joined = "\n".join(patch.transition_messages_four_agent(previous, current))
    assert "FOUR-AGENT STRATEGY REVIEW STARTED" in joined
    assert "GPT" in joined
    assert "Gemini" in joined
    assert "Copilot" in joined
    assert "Claude" in joined
    assert "THREE-AGENT STRATEGY REVIEW STARTED" not in joined


def test_three_agents_complete_does_not_claim_four_complete():
    previous = _state(gpt="WAITING", gemini="WAITING", copilot="WAITING", claude="WAITING")
    current = _state(claude="WAITING")
    joined = "\n".join(patch.transition_messages_four_agent(previous, current))
    assert "THREE STRATEGY AGENTS COMPLETE" not in joined
    assert "FOUR STRATEGY AGENTS COMPLETE" not in joined


def test_claude_completion_triggers_four_agent_completion_even_after_original_three():
    previous = _state(claude="WAITING")
    current = _state(claude="DONE")
    joined = "\n".join(patch.transition_messages_four_agent(previous, current))
    assert "FOUR STRATEGY AGENTS COMPLETE" in joined
    assert "GPT ✅" in joined
    assert "Gemini ✅" in joined
    assert "Copilot ✅" in joined
    assert "Claude ✅" in joined


def test_strategy_page_shows_four_agents_and_true_completion():
    text_waiting = patch.strategy_text_four_agent(_state(claude="WAITING"))
    assert "FOUR-AGENT STRATEGY REVIEW" in text_waiting
    assert "Claude:" in text_waiting
    assert "All four complete: <b>NO</b>" in text_waiting

    text_done = patch.strategy_text_four_agent(_state(claude="DONE"))
    assert "All four complete: <b>YES</b>" in text_done


def test_blocked_provider_uses_warning_icon():
    state = _state(copilot="BLOCKED_AUTH", claude="WAITING")
    text = patch.strategy_text_four_agent(state)
    assert "Copilot: ⚠️" in text
    assert "Claude: ⏳" in text
