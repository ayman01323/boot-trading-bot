from __future__ import annotations

import sqlite3
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_owner_changeset_4_exit_safety_patch as exit_safety
from learnerbot import solana_owner_changeset_4_patch as owner


def test_changeset_stamp_and_limits():
    assert owner.CHANGESET_ID == "CHANGE_SET_4"
    assert owner.CHANGESET_APPROVED_UTC == "2026-08-29T10:38:58Z"
    assert owner.OWNER_LIVE_TRADE_SOL == Decimal("0.005")
    assert owner.OWNER_MAX_LIVE_POSITIONS == 10
    assert owner.OWNER_FORCE_EXIT_SECONDS == 33 * 60


def test_timed_exit_uses_existing_safe_slice_backoff():
    assert exit_safety.CHANGESET4_TIMED_EXIT_REASON == "SOLANA_OWNER_CHANGESET4_33M_FULL_EXIT"
    assert exit_safety.CHANGESET4_TIMED_EXIT_REASON in exit_safety._emergency._LOSS_EXIT_REASONS


def test_live_limits_pin_trade_but_preserve_reserve(monkeypatch):
    monkeypatch.setattr(
        owner,
        "_PREV_LIVE_LIMITS",
        lambda app, telegram_id, cfg=None: (Decimal("0.009"), Decimal("0.02")),
    )
    trade, reserve = owner.live_limits_owner_changeset_4(object(), "1", {})
    assert trade == Decimal("0.005")
    assert reserve == Decimal("0.02")


def test_lp_concentration_becomes_revalidation_not_automatic_refusal(monkeypatch):
    monkeypatch.setattr(
        owner,
        "_PREV_RUGCHECK",
        lambda summary, cfg: {
            "decision": "SHADOW_ONLY",
            "reason_code": "LP_CONCENTRATION_RISK",
            "reason": "locked=0%",
            "evidence": {"rugcheck_lp_locked_pct": Decimal("0")},
        },
    )
    result = owner.evaluate_rugcheck_lp_revalidation({}, {})
    assert result["decision"] == "PASS"
    assert result["reason_code"] == "LP_REVALIDATION_REQUIRED"
    assert result["evidence"]["lp_revalidation_required"] is True


def test_structural_rugcheck_hard_block_is_unchanged(monkeypatch):
    hard = {
        "decision": "HARD_BLOCK",
        "reason_code": "TOKEN_SECURITY_SEVERE",
        "reason": "mint authority",
        "evidence": {"rugcheck_blocking_risk": "mint authority"},
    }
    monkeypatch.setattr(owner, "_PREV_RUGCHECK", lambda summary, cfg: hard)
    assert owner.evaluate_rugcheck_lp_revalidation({}, {}) == hard


def test_capacity_allows_ninth_but_blocks_eleventh_candidate(monkeypatch):
    app = SimpleNamespace(csv_dir="unused")
    event = {"leader_wallet": "leader", "mint": "mint"}
    monkeypatch.setattr(owner._live, "all_users", lambda csv_dir, enabled_only=True: [{"telegram_id": "1"}])
    monkeypatch.setattr(owner._live, "live_enabled", lambda app, tid: True)
    monkeypatch.setattr(owner._sol._sibot, "user_settings", lambda app, tid, chain_id=0: {"enabled": "true"})
    monkeypatch.setattr(owner._sol, "_leader_rank", lambda app, tid, wallet: 1)
    monkeypatch.setattr(owner._sol, "_open_position", lambda app, tid, mint: False)
    monkeypatch.setattr(owner._live, "live_limits", lambda app, tid, cfg=None: (Decimal("0.005"), Decimal("0.02")))

    monkeypatch.setattr(owner._live, "_open_live_count", lambda app, tid: 9)
    assert owner.eligible_live_users_owner_changeset_4(app, event, {"live_max_positions": "10"}) == [
        ("1", Decimal("0.005"))
    ]

    monkeypatch.setattr(owner._live, "_open_live_count", lambda app, tid: 10)
    assert owner.eligible_live_users_owner_changeset_4(app, event, {"live_max_positions": "10"}) == []


def _db_connect(path):
    def connect(_app):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    return connect


def test_33_minute_remaining_position_requests_protected_full_exit(monkeypatch, tmp_path):
    db = tmp_path / "positions.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE positions(position_id TEXT, telegram_id TEXT, entry_ts INTEGER, status TEXT, mode TEXT)"
    )
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?)",
        ("p1", "1", 20, "OPEN", "LIVE"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(owner, "_PREV_MONITOR", lambda app: "base-monitor-ran")
    monkeypatch.setattr(owner._sol, "connect", _db_connect(str(db)))
    monkeypatch.setattr(owner._live, "live_enabled", lambda app, tid: True)
    monkeypatch.setattr(owner.time, "time", lambda: 2000)  # exactly 33 minutes after entry_ts=20

    calls = []
    monkeypatch.setattr(
        owner._live,
        "_close_live",
        lambda app, tid, position, fraction, reason: calls.append((tid, position["position_id"], fraction, reason)),
    )

    result = owner.monitor_positions_owner_changeset_4(SimpleNamespace())
    assert result == "base-monitor-ran"
    assert calls == [
        ("1", "p1", Decimal(1), "SOLANA_OWNER_CHANGESET4_33M_FULL_EXIT")
    ]


def test_before_33_minutes_does_not_force_exit(monkeypatch, tmp_path):
    db = tmp_path / "positions.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE positions(position_id TEXT, telegram_id TEXT, entry_ts INTEGER, status TEXT, mode TEXT)"
    )
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?)",
        ("p1", "1", 21, "OPEN", "LIVE"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(owner, "_PREV_MONITOR", lambda app: None)
    monkeypatch.setattr(owner._sol, "connect", _db_connect(str(db)))
    monkeypatch.setattr(owner._live, "live_enabled", lambda app, tid: True)
    monkeypatch.setattr(owner.time, "time", lambda: 2000)  # 1979 seconds

    calls = []
    monkeypatch.setattr(owner._live, "_close_live", lambda *args, **kwargs: calls.append(True))
    owner.monitor_positions_owner_changeset_4(SimpleNamespace())
    assert calls == []
