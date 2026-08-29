from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(text)
    return text


def test_reject_reporting_is_persistent_once_only() -> None:
    text = _source("learnerbot/solana_reject_once_reporting_patch.py")
    assert 'DEDUP_POLICY = "once_per_account_mint_leader_reason"' in text
    assert '"telegram_reject_once:v1:"' in text
    assert "hashlib.sha256" in text
    assert "_sol._state(conn, key" in text
    assert "_sol._set_state(conn, key" in text
    assert "signature_in_key=false" in text
    assert "_REJECT_REPORT_SUPPRESS_SECONDS" not in text


def test_same_signal_is_not_part_of_condition_identity() -> None:
    text = _source("learnerbot/solana_reject_once_reporting_patch.py")
    start = text.index("def _condition_identity")
    end = text.index("\n\ndef _already_sent", start)
    body = text[start:end]
    assert "mint" in body
    assert "leader" in body
    assert "reason" in body
    assert "signature" not in body
    assert "event_id" not in body


def test_lp_unlocked_is_revalidation_not_standalone_refusal() -> None:
    text = _source("learnerbot/solana_owner_changeset_4_patch.py")
    assert '!= "LP_CONCENTRATION_RISK"' in text
    assert '"PASS",\n        "LP_REVALIDATION_REQUIRED"' in text
    assert 'evidence["lp_revalidation_required"] = True' in text


def test_reporting_layer_composes_after_changeset4_integrity() -> None:
    text = _source("learnerbot/solana_owner_changeset_4_integrity_patch.py")
    assert text.index("install()") < text.rindex("solana_reject_once_reporting_patch")
