from pathlib import Path

import pytest

from learnerbot import master_change_council as council


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_master_change_rejects_embedded_secret_values() -> None:
    with pytest.raises(ValueError, match="credentials"):
        council._clean_request("please use sk-abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(ValueError, match="credentials"):
        council._clean_request("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")


def test_master_change_protection_is_fail_closed_for_sensitive_subjects() -> None:
    hard, protected = council.protection_reasons("change the wallet signing private key and deploy it live")
    assert "private key" in hard
    assert "wallet signing" in hard
    assert "wallet" in protected
    assert "deploy" in protected


def test_gpt_decision_paths_are_exact_and_bounded() -> None:
    decision = council._normalise_decision({
        "action": "IMPLEMENT",
        "risk_class": "LOW",
        "summary": "x",
        "reasoning": "y",
        "allowed_files": ["learnerbot/telegram_x.py", "../escape.py", "tests/*.py", "learnerbot/telegram_x.py"],
        "required_tests": ["pytest -q"],
        "auto_merge_recommended": True,
    })
    assert decision["allowed_files"] == ["learnerbot/telegram_x.py"]
    assert decision["action"] == "IMPLEMENT"
    assert decision["risk_class"] == "LOW"


def test_telegram_command_is_master_only_and_uses_five_agent_flow() -> None:
    text = _text("learnerbot/telegram_master_change_patch.py")
    assert 'cmd == "/aichange"' in text
    assert "_require_master(app, tid)" in text
    assert "Claude + Gemini + DeepSeek + Copilot" in text
    assert "GPT then makes the final decision" in text
    assert "retry" in text


def test_local_council_requires_every_adviser_before_gpt_implementation() -> None:
    text = _text("learnerbot/master_change_council.py")
    for agent in ("claude", "gemini", "deepseek", "copilot"):
        assert f'"{agent}"' in text
    assert "all_advisers_replied" in text
    assert "if not all_ok" in text
    assert "implementation_allowed" in text
    assert "_call_final_gpt" in text


def test_bridge_publishes_only_sanitised_change_and_dispatches_once() -> None:
    text = _text(".github/workflows/master-change-council-bridge.yml")
    assert "master_change_council_latest.json" in text
    assert "master-change/requests/${REQUEST_ID}.json" in text
    assert "master-change/dispatch_state.json" in text
    assert "gpt-master-change-implement.yml" in text
    assert "implementation_nonce" in text
    assert "requester_chat_id" not in text
    assert "[REDACTED]" in text


def test_gpt_implementation_has_file_gate_tests_and_no_direct_secret_authority() -> None:
    text = _text(".github/workflows/gpt-master-change-implement.yml")
    assert "all adviser replies are required" in text
    assert "GPT final decision is not IMPLEMENT" in text
    assert "stale council evidence" in text
    assert "GPT changed paths outside the adjudicated allow-list" in text
    assert "Hard-protected paths cannot be changed" in text
    assert "python3 -m compileall -q learnerbot scripts" in text
    assert "python3 -m pytest -q" in text
    assert "gh pr create --draft" in text
    assert "Deterministic auto-merge gate: NO" in text
    assert "protected_reasons" in text
    # GPT-suggested required_tests are evidence only; they are never eval/exec shell input.
    assert "required_tests" not in text.split("Enforce path boundary and run full tests", 1)[1]
    assert "eval " not in text


def test_master_change_workflows_never_use_arbitrary_sudo() -> None:
    for path in (
        ".github/workflows/master-change-council-bridge.yml",
        ".github/workflows/gpt-master-change-implement.yml",
    ):
        text = _text(path)
        assert "sudo " not in text
        assert "PRIVATE_KEY" not in text
        assert "mnemonic" not in text.lower()
