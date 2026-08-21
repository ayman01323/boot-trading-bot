from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _runner():
    sys.path.insert(0, str(SCRIPTS))
    try:
        sys.modules.pop("resilient_selected_master_v2", None)
        return importlib.import_module("resilient_selected_master_v2")
    finally:
        if str(SCRIPTS) in sys.path:
            sys.path.remove(str(SCRIPTS))


def test_selected_master_fallback_order_is_exact_and_no_provider_repeats() -> None:
    r = _runner()
    assert r._provider_order("copilot") == ["copilot", "gpt", "claude", "gemini"]
    assert r._provider_order("gemini") == ["gemini", "gpt", "claude", "copilot"]
    assert r._provider_order("claude") == ["claude", "gpt", "gemini", "copilot"]
    assert r._provider_order("gpt") == ["gpt", "claude", "gemini", "copilot"]
    assert r._provider_order("auto") == ["gpt", "claude", "gemini", "copilot"]
    for preferred in ("gpt", "claude", "gemini", "copilot", "auto"):
        order = r._provider_order(preferred)
        assert len(order) == len(set(order)) == 4


def test_master_runner_allows_one_valid_report_but_keeps_stricter_single_agent_policy() -> None:
    text = (ROOT / "scripts/resilient_selected_master.py").read_text(encoding="utf-8")
    assert '"minimum_valid_reports_to_continue": 1' in text
    assert 'threshold = 0.95 if count == 1 else 0.85' in text
    assert 'single_agent_engineering_requires_deterministic_evidence' in text
    assert '"live_trading_depends_on_ai_health": False' in text
    assert 'if not reports:' in text


def test_copilot_fallback_uses_official_noninteractive_plan_mode() -> None:
    text = (ROOT / "scripts/resilient_selected_master_v2.py").read_text(encoding="utf-8")
    assert '"--plan"' in text
    assert '"-p"' in text
    assert '"-s"' in text
    assert 'env["GH_TOKEN"] = token' in text
    assert 'env["GITHUB_TOKEN"] = token' in text


def test_telegram_master_menu_controls_both_lanes_and_run_now() -> None:
    text = (ROOT / "learnerbot/telegram_ai_reports_menu_patch.py").read_text(encoding="utf-8")
    for provider in ("auto", "gpt", "gemini", "copilot", "claude"):
        assert f'mbtn("strategy", "{provider}")' in text
        assert f'mbtn("engineering", "{provider}")' in text
    assert 'aicfg:run:strategy' in text
    assert 'aicfg:run:engineering' in text
    assert 'aicfg:run:both' in text
    assert 'MASTER only' in text


def test_control_bridge_is_sanitised_and_contains_no_credentials() -> None:
    text = (ROOT / "learnerbot/ai_master_control.py").read_text(encoding="utf-8")
    assert '/var/tmp/boot/ai_master_control.json' in text
    assert 'strategy_master' in text and 'engineering_master' in text
    assert 'strategy_run_nonce' in text and 'engineering_run_nonce' in text
    assert 'API_KEY' not in text
    assert 'PRIVATE_KEY' not in text


def test_four_agent_health_warns_but_does_not_stop_trading() -> None:
    text = (ROOT / "learnerbot/ai_four_agent_health_patch.py").read_text(encoding="utf-8")
    assert 'PROVIDERS = ("gpt", "claude", "gemini", "copilot")' in text
    assert 'cycle continued with {n}/4 valid report(s)' in text
    assert 'AI failure never disables the trading engine' in text
    assert 'selected MASTER → GPT → Claude → Gemini → other available agent' in text


def test_documented_contract_matches_runtime_resilience_and_safety() -> None:
    text = (ROOT / "docs/AI_MASTER_CONTROL.md").read_text(encoding="utf-8")
    assert 'selected MASTER -> GPT -> Claude -> Gemini -> other available agent' in text
    assert 'One valid independent report is sufficient' in text
    assert 'AI health never disables LIVE trading' in text
