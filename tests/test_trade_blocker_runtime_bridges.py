from decimal import Decimal
from types import SimpleNamespace

from learnerbot import evm_history_runtime_secret_patch as secret_patch
from learnerbot import solana_trade_gate_truth_patch as sol_truth
from learnerbot import trade_blocker_secret_redaction_patch as redaction


def test_runtime_etherscan_secret_reads_json_without_logging_value(tmp_path, monkeypatch):
    runtime = tmp_path / "evm.env"
    runtime.write_text('ETHERSCAN_API_KEY="secret-value"\n', encoding="utf-8")
    monkeypatch.setattr(secret_patch, "_RUNTIME_FILE", runtime)
    assert secret_patch._runtime_etherscan_key() == "secret-value"


def test_runtime_etherscan_secret_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_patch, "_RUNTIME_FILE", tmp_path / "missing.env")
    assert secret_patch._runtime_etherscan_key() == ""


def test_trade_blocker_redacts_explorer_and_provider_credentials():
    text = (
        "HTTP 429 https://api.etherscan.io/v2/api?chainid=137&apikey=ABC123SECRET "
        "OPENAI_API_KEY=sk-example-secret"
    )
    safe = redaction._redact(text)
    assert "ABC123SECRET" not in safe
    assert "sk-example-secret" not in safe
    assert "apikey=<redacted>" in safe


def test_solana_gate_truth_report_surfaces_hidden_preflight_blocks(monkeypatch):
    monkeypatch.setattr(sol_truth, "_PREV_BUILD_REPORT", lambda app, tid: "BASE REPORT")
    monkeypatch.setattr(
        sol_truth,
        "gate_snapshot",
        lambda app, tid: {
            "wallet": {
                "signing_ready": True,
                "balance_sol": Decimal("0.006"),
                "minimum_sol": Decimal("0.0055"),
                "reason": "wallet signing and minimum funding are ready",
            },
            "platform_ok": False,
            "platform_reason": "recovery cooldown 120 min",
            "platform_metrics": {"profit_factor": Decimal("0.84")},
            "recovery_canary": False,
            "leaders": [
                {
                    "rank": 1,
                    "wallet": "ABCDEFGH1234567890ZYXWVUT",
                    "ok": False,
                    "reason": "leader recent median return 2% is below LIVE edge floor 4%",
                }
            ],
        },
    )
    text = sol_truth.build_report_with_gate_truth(SimpleNamespace(), "master")
    assert "SIGNING READY" in text
    assert "Platform amount-profit gate: <b>BLOCK</b>" in text
    assert "recovery cooldown 120 min" in text
    assert "Leader #1" in text and "<b>BLOCK</b>" in text
    assert "read-only" in text
