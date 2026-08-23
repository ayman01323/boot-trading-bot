from pathlib import Path

import pytest
import yaml

from learnerbot import master_change_council as council
from scripts import master_change_policy as policy


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _evidence(**overrides):
    # schema_version=1 deliberately represents legacy evidence. The deterministic
    # policy keeps requiring all four advisers for those already-created requests.
    value = {
        "schema_version": 1,
        "request_id": "mc-20260822T100000Z-abcdef",
        "implementation_nonce": 1,
        "implementation_allowed": True,
        "hard_protected_reasons": [],
        "protected_reasons": [],
        "all_advisers_replied": True,
        "source_sha": "a" * 40,
        "auto_merge_allowed": True,
        "advisers": {
            name: {"acknowledged": True, "provider_rc": 0, "reply": "APPROVE: bounded change"}
            for name in ("claude", "gemini", "deepseek", "copilot")
        },
        "gpt_decision": {
            "action": "IMPLEMENT",
            "risk_class": "LOW",
            "allowed_files": ["learnerbot/telegram_example_patch.py"],
        },
    }
    value.update(overrides)
    return value


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


def test_telegram_command_is_master_only_and_exposes_cost_router() -> None:
    text = _text("learnerbot/telegram_master_change_patch.py")
    assert 'cmd == "/aichange"' in text
    assert 'cmd == "/aicost"' in text
    assert "_require_master(app, tid)" in text
    assert "Cost Router" in text
    assert "Critical trading/security/deployment requests still use the full council" in text
    assert "Successful adviser replies will be reused" in text


def test_local_council_uses_required_advisers_and_keeps_gpt_final() -> None:
    text = _text("learnerbot/master_change_cost_router_patch.py")
    assert "master_change_route" in text
    assert "required_advisers" in text
    assert "_call_final_gpt" in text
    assert "reused" in text
    assert "implementation_allowed" in text
    for agent in ("claude", "gemini", "deepseek", "copilot"):
        assert agent in _text("learnerbot/ai_cost_router.py")


def test_existing_telegram_publisher_carries_change_council_without_second_polling_job() -> None:
    text = _text(".github/workflows/publish-ai-master-control.yml")
    assert "master_change_council_latest.json" in text
    assert "master_change_publisher_state.json" in text
    assert "zero extra GitHub calls" in text
    assert "master-change/requests/${request_id}.json" in text
    assert "master-change/dispatch_state.json" in text
    assert "gpt-master-change-implement.yml" in text
    assert "requester_chat_id" not in text
    assert "[REDACTED]" in text
    assert not (ROOT / ".github/workflows/master-change-council-bridge.yml").exists()


def test_master_change_workflow_yaml_is_parseable() -> None:
    for path in (
        ".github/workflows/publish-ai-master-control.yml",
        ".github/workflows/gpt-master-change-implement.yml",
        ".github/workflows/master-change-council-protected-deploy.yml",
    ):
        assert yaml.compose(_text(path)) is not None, path


def test_gpt_implementation_has_python311_file_gate_and_full_tests() -> None:
    text = _text(".github/workflows/gpt-master-change-implement.yml")
    assert "actions/setup-python@v5" in text
    assert "python-version: '3.11'" in text
    assert "scripts/master_change_policy.py validate-request" in text
    assert "scripts/master_change_policy.py validate-changed" in text
    assert "python -m compileall -q learnerbot scripts" in text
    assert "python -m pytest -q" in text
    assert "gh pr create --draft" in text
    assert "Deterministic auto-merge gate: NO" in text
    assert "eval " not in text


def test_policy_rejects_council_self_modification_before_gpt_code_call() -> None:
    evidence = _evidence()
    evidence["gpt_decision"]["allowed_files"] = ["learnerbot/master_change_council.py"]
    with pytest.raises(ValueError, match="cannot authorise modification"):
        policy.validate_request(
            evidence,
            request_id=evidence["request_id"],
            nonce=1,
            current_sha="a" * 40,
        )
    for path in (
        ".github/workflows/gpt-master-change-implement.yml",
        ".github/workflows/publish-ai-master-control.yml",
        ".github/workflows/master-change-council-protected-deploy.yml",
        "learnerbot/master_change_council.py",
        "learnerbot/master_change_cost_router_patch.py",
        "learnerbot/ai_cost_router.py",
        "learnerbot/ai_cost_provider_patch.py",
        "learnerbot/telegram_master_change_patch.py",
        "learnerbot/ai_agent_ws_runtime_patch.py",
        "scripts/ai_agent_ws_bus.py",
        "scripts/ai_agent_ws_worker.py",
        "scripts/ai_agent_ws_send.py",
        "scripts/master_change_policy.py",
        "tests/test_master_change_council.py",
        "tests/test_ai_cost_router.py",
    ):
        assert path in policy.GOVERNANCE_FILES


def test_policy_rejects_stale_or_incomplete_evidence() -> None:
    evidence = _evidence()
    with pytest.raises(ValueError, match="stale council evidence"):
        policy.validate_request(evidence, request_id=evidence["request_id"], nonce=1, current_sha="b" * 40)
    evidence = _evidence(all_advisers_replied=False)
    with pytest.raises(ValueError, match="all required adviser replies"):
        policy.validate_request(evidence, request_id=evidence["request_id"], nonce=1, current_sha="a" * 40)


def test_legacy_evidence_still_requires_every_adviser() -> None:
    evidence = _evidence()
    del evidence["advisers"]["gemini"]
    with pytest.raises(ValueError, match="gemini required adviser"):
        policy.validate_request(evidence, request_id=evidence["request_id"], nonce=1, current_sha="a" * 40)


def test_low_risk_auto_merge_cannot_be_test_only_or_protected() -> None:
    evidence = _evidence()
    assert policy.auto_merge_eligible(evidence, ["learnerbot/telegram_example_patch.py", "tests/test_example.py"])
    assert not policy.auto_merge_eligible(evidence, ["tests/test_example.py"])
    protected = _evidence(protected_reasons=["wallet"])
    assert not policy.auto_merge_eligible(protected, ["learnerbot/telegram_example_patch.py"])


def test_master_change_runtime_workflows_never_use_arbitrary_sudo_or_secret_credentials() -> None:
    for path in (
        ".github/workflows/publish-ai-master-control.yml",
        ".github/workflows/gpt-master-change-implement.yml",
    ):
        text = _text(path)
        assert "sudo " not in text
        assert "PRIVATE_KEY" not in text
        assert "secrets.PRIVATE" not in text
        assert "secrets.WALLET" not in text
        assert "secrets.MNEMONIC" not in text


def test_deploy_workflow_uses_only_existing_restricted_root_wrappers() -> None:
    text = _text(".github/workflows/master-change-council-protected-deploy.yml")
    assert 'sudo /usr/local/sbin/deploy-boot-trading-bot "$target"' in text
    assert "sudo /usr/local/sbin/status-boot-trading-bot" in text
    for forbidden in ("sudo bash", "sudo sh", "sudo -i", "sudo su", "PRIVATE_KEY", "secrets.WALLET"):
        assert forbidden not in text
    assert "routing_model_calls':0" in text
    assert "expected=[^[:space:]]*aichange" in text
