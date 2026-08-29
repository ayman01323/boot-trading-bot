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


def test_pre_invariant_quality_remains_audited_moderate_profile() -> None:
    text = _source("learnerbot/solana_leader_quality_restore_patch.py")
    assert '"min_profit_factor": "1.35"' in text
    assert '"min_recent_win_rate_pct": "55"' in text
    assert '"min_copied_trades_for_guard": "2"' in text
    assert '"min_copied_profit_factor": "1.50"' in text
    assert '"leader_suspend_minutes": "1440"' in text
    assert "OWNER_20260829_HISTORICAL_1230_WITH_EXIT_OVERRIDES" not in text


def test_owner_historical_profile_is_late_bound_in_changeset4() -> None:
    text = _source("learnerbot/solana_owner_changeset_4_patch.py")
    assert "def settings_owner_changeset_4" in text
    assert '"leaders_per_user": "5"' in text
    assert '"min_profit_factor": "1.20"' in text
    assert '"min_recent_win_rate_pct": "50"' in text
    assert '"min_copied_trades_for_guard": "5"' in text
    assert '"min_copied_profit_factor": "1.0"' in text
    assert '"take_profit_pct": "15"' in text
    assert '"max_hold_hours": "0.5"' in text
    assert '"live_trade_sol": "0.005"' in text
    assert '"live_max_positions": "10"' in text
    assert "_sol.settings = settings_owner_changeset_4" in text
    assert "_guard._copied_ok = _first_day.copied_ok_first_day" in text


def test_reporting_layer_composes_after_changeset4_integrity() -> None:
    text = _source("learnerbot/solana_owner_changeset_4_integrity_patch.py")
    assert '"pre_invariant_quality_preserved"' in text
    assert '"historical_profile_final_outer"' in text
    assert text.index("install()") < text.rindex("solana_reject_once_reporting_patch")
