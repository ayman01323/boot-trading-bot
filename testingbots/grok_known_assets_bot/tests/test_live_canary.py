from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from grok_known_assets_bot import control, live_canary as lc
from grok_known_assets_bot import live_canary_runner as runner
from grok_known_assets_bot.core import Journal


def _db(tmp_path: Path) -> sqlite3.Connection:
    j = Journal(str(tmp_path / "state.sqlite3"))
    lc.ensure_schema(j.db)
    return j.db


def _evidence() -> dict:
    return {"route_id": "poolA", "roundtrip_loss_pct": 1.0}


# --------------------------------------------------------------------------- #
# Ledger invariants
# --------------------------------------------------------------------------- #

def test_amount_above_hard_cap_is_refused(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(lc.CanaryLedgerError) as exc:
        lc.create_pending_entry(
            db, asset_key="solana:SOL:NATIVE", mint="M",
            input_micro_usdc=1_000_000, target_lamports=lc.HARD_CAP_LAMPORTS + 1,
            min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
        )
    assert "AMOUNT_EXCEEDS_HARD_CAP" in str(exc.value)


def test_only_one_nonterminal_entry_ticket(tmp_path):
    db = _db(tmp_path)
    lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    with pytest.raises(lc.CanaryLedgerError):
        lc.create_pending_entry(
            db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
            min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1001,
        )


def test_only_one_open_position(tmp_path):
    db = _db(tmp_path)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    lc.approve_entry(db, aid, user_id="u1", chat_id="c1", now=1001)
    t = lc.claim_next_approved(db, now=1002)
    assert t["approval_id"] == aid
    lc.mark_broadcast_submitted(db, aid, now=1003)
    lc.mark_confirmed(db, aid, tx_signature="sig1", acquired_lamports=lc.TARGET_LAMPORTS, now=1004)
    with pytest.raises(lc.CanaryLedgerError):
        lc.create_pending_entry(
            db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
            min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1100,
        )


def test_expired_approval_is_rejected(tmp_path):
    db = _db(tmp_path)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000, ttl_seconds=30,
    )
    with pytest.raises(lc.CanaryLedgerError):
        lc.approve_entry(db, aid, user_id="u1", chat_id="c1", now=1000 + 31)
    assert lc._row(db, aid)["status"] == lc.STATUS_EXPIRED


def test_unknown_and_wrong_state_approvals_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(lc.CanaryLedgerError):
        lc.approve_entry(db, "nope", user_id="u", chat_id="c", now=1)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    lc.approve_entry(db, aid, user_id="u", chat_id="c", now=1001)
    with pytest.raises(lc.CanaryLedgerError):  # already approved, not pending
        lc.approve_entry(db, aid, user_id="u", chat_id="c", now=1002)


def test_claim_is_single_shot(tmp_path):
    db = _db(tmp_path)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    lc.approve_entry(db, aid, user_id="u", chat_id="c", now=1001)
    assert lc.claim_next_approved(db, now=1002)["approval_id"] == aid
    assert lc.claim_next_approved(db, now=1003) is None


def test_reconcile_on_start_invalidates_nonterminal(tmp_path):
    db = _db(tmp_path)
    p = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    lc.approve_entry(db, p, user_id="u", chat_id="c", now=1001)
    lc.claim_next_approved(db, now=1002)  # -> EXECUTING
    out = lc.reconcile_on_start(db, now=2000)
    assert out["reconciliation_required"] == 1
    assert lc._row(db, p)["status"] == lc.STATUS_RECONCILIATION_REQUIRED
    assert lc.needs_reconciliation(db) is True


def test_cancel_unclaimed_never_touches_executing(tmp_path):
    db = _db(tmp_path)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000,
    )
    lc.approve_entry(db, aid, user_id="u", chat_id="c", now=1001)
    lc.claim_next_approved(db, now=1002)  # EXECUTING
    assert lc.cancel_unclaimed(db, reason="stop", now=1003) == 0
    assert lc._row(db, aid)["status"] == lc.STATUS_EXECUTING


def test_expire_stale_only_pending(tmp_path):
    db = _db(tmp_path)
    aid = lc.create_pending_entry(
        db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence=_evidence(), now=1000, ttl_seconds=30,
    )
    assert lc.expire_stale(db, now=1000 + 31) == 1
    assert lc._row(db, aid)["status"] == lc.STATUS_EXPIRED


# --------------------------------------------------------------------------- #
# Runner: broadcast only via an approved, claimed ticket; failure handling
# --------------------------------------------------------------------------- #

class _FakeAsset:
    key = "solana:SOL:NATIVE"
    enabled = True


class _FakeFeed:
    class settings:  # noqa: D106
        slippage_bps = 50

    def supported(self, asset):  # noqa: D401
        return True

    def collect(self, asset, *, now):
        class _Env:
            snapshot = object()
        return _Env()


def _ready(**over):
    base = dict(ready=True, reason="PASS", entry_min_out_lamports=800_000, entry_input_micro_usdc=1_800_000)
    base.update(over)
    class _R:
        ready = base["ready"]
        reason = base["reason"]
        entry_min_out_lamports = base["entry_min_out_lamports"]
        entry_input_micro_usdc = base["entry_input_micro_usdc"]
    return _R()


def _seed_approved(db, *, now=1000):
    aid = lc.create_pending_entry(
        db, asset_key="solana:SOL:NATIVE", mint=runner.SOL_MINT, input_micro_usdc=1_800_000,
        target_lamports=lc.TARGET_LAMPORTS, min_out_lamports=800_000, slippage_bps=50,
        evidence=_evidence(), now=now,
    )
    lc.approve_entry(db, aid, user_id="u", chat_id="c", now=now + 1)
    return aid


def _prep_control(tmp_path, monkeypatch, *, canary=True):
    cf = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(cf))
    control.save_state(armed=True, live_readiness_enabled=True, live_canary_enabled=canary, updated_by="test")
    return cf


def test_no_broadcast_without_approved_ticket(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    db = _db(tmp_path)
    j = Journal(str(tmp_path / "state.sqlite3"))
    calls = []
    monkeypatch.setattr(runner, "_execute_ticket", lambda *a, **k: calls.append(1))
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=1000)
    assert calls == []


def test_happy_path_entry_confirms(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    monkeypatch.setattr(runner, "assess_live_readiness", lambda *a, **k: _ready())
    import grok_known_assets_bot.live_execution as lx
    monkeypatch.setattr(lx, "preflight_funding", lambda **k: (True, "ok"))

    def fake_swap(*, input_mint, output_mint, amount_raw, min_out_raw, on_broadcast_submitted=None, executor=None):
        on_broadcast_submitted()
        return {"signature": "SIG", "out_raw": 8_900_000, "wallet_delta_lamports": 8_800_000}

    monkeypatch.setattr(lx, "execute_swap", fake_swap)
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=2000)
    assert lc._row(db, aid)["status"] == lc.STATUS_CONFIRMED
    assert lc._row(db, aid)["tx_signature"] == "SIG"


def test_pre_broadcast_failure_is_simulation_failed_and_canary_stays(tmp_path, monkeypatch):
    cf = _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    monkeypatch.setattr(runner, "assess_live_readiness", lambda *a, **k: _ready())
    import grok_known_assets_bot.live_execution as lx
    monkeypatch.setattr(lx, "preflight_funding", lambda **k: (True, "ok"))

    def fake_swap(**k):
        raise lx.ExecPreBroadcastError("simulation failed")

    monkeypatch.setattr(lx, "execute_swap", fake_swap)
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=2000)
    assert lc._row(db, aid)["status"] == lc.STATUS_SIMULATION_FAILED
    assert control.is_live_canary_enabled(cf) is True


def test_ambiguous_failure_pauses_canary(tmp_path, monkeypatch):
    cf = _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    monkeypatch.setattr(runner, "assess_live_readiness", lambda *a, **k: _ready())
    import grok_known_assets_bot.live_execution as lx
    monkeypatch.setattr(lx, "preflight_funding", lambda **k: (True, "ok"))

    def fake_swap(*, on_broadcast_submitted=None, **k):
        on_broadcast_submitted()
        raise lx.ExecAmbiguousError("post-send RPC timeout")

    monkeypatch.setattr(lx, "execute_swap", fake_swap)
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=2000)
    assert lc._row(db, aid)["status"] == lc.STATUS_UNKNOWN_OUTCOME
    assert control.is_live_canary_enabled(cf) is False


def test_post_land_unproven_requires_reconciliation(tmp_path, monkeypatch):
    cf = _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    monkeypatch.setattr(runner, "assess_live_readiness", lambda *a, **k: _ready())
    import grok_known_assets_bot.live_execution as lx
    monkeypatch.setattr(lx, "preflight_funding", lambda **k: (True, "ok"))

    def fake_swap(*, on_broadcast_submitted=None, **k):
        on_broadcast_submitted()
        raise lx.ExecPostLandError("landed unproven", "LANDEDSIG")

    monkeypatch.setattr(lx, "execute_swap", fake_swap)
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=2000)
    row = lc._row(db, aid)
    assert row["status"] == lc.STATUS_RECONCILIATION_REQUIRED
    assert control.is_live_canary_enabled(cf) is False
    assert lc.needs_reconciliation(db) is True


def test_reconciliation_blocks_further_execution(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    # Force a reconciliation-required row.
    lc.claim_next_approved(db, now=1100)
    lc.mark_broadcast_submitted(db, aid, now=1101)
    lc._transition(db, aid, expected=lc.STATUS_BROADCAST_SUBMITTED, new_status=lc.STATUS_UNKNOWN_OUTCOME, now=1102)
    other = lc.create_pending_entry(
        db, asset_key="solana:SOL:NATIVE", mint=runner.SOL_MINT, input_micro_usdc=1_800_000,
        target_lamports=lc.TARGET_LAMPORTS, min_out_lamports=800_000, slippage_bps=50,
        evidence=_evidence(), now=1200,
    )
    lc.approve_entry(db, other, user_id="u", chat_id="c", now=1201)
    called = []
    monkeypatch.setattr(runner, "_execute_ticket", lambda *a, **k: called.append(1))
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=1300)
    assert called == []


def test_revalidation_route_degradation_rejects(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    aid = _seed_approved(db)
    monkeypatch.setattr(runner, "assess_live_readiness", lambda *a, **k: _ready(entry_min_out_lamports=1))
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=2000)
    assert lc._row(db, aid)["status"] == lc.STATUS_REJECTED_REVALIDATION


# --------------------------------------------------------------------------- #
# Control-file boundaries
# --------------------------------------------------------------------------- #

def test_canary_cannot_be_on_without_arm_and_readiness(tmp_path, monkeypatch):
    cf = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(cf))
    state = control.save_state(armed=False, live_readiness_enabled=False, live_canary_enabled=True, updated_by="t")
    assert state["live_canary_enabled"] is False
    assert state["live_money_enabled"] is False
    state = control.save_state(armed=True, live_readiness_enabled=True, live_canary_enabled=True, updated_by="t")
    assert state["live_money_enabled"] is True


def test_ingest_skips_stale_live_ready(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    j.event("LIVE_READY", "solana:SOL:NATIVE", {
        "ready": True, "expires_epoch": 100, "entry_input_micro_usdc": 1_800_000,
        "entry_min_out_lamports": 800_000, "slippage_bps": 50,
    })
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=5000)
    assert lc.list_pending(db) == []


def test_ingest_creates_pending_from_fresh_live_ready(tmp_path, monkeypatch):
    _prep_control(tmp_path, monkeypatch)
    j = Journal(str(tmp_path / "state.sqlite3"))
    db = j.db
    lc.ensure_schema(db)
    j.event("LIVE_READY", "solana:SOL:NATIVE", {
        "ready": True, "expires_epoch": 9999, "entry_input_micro_usdc": 1_800_000,
        "entry_min_out_lamports": 800_000, "slippage_bps": 50, "route_id": "p",
    })
    runner.run_once(j, db, _FakeFeed(), {"solana:SOL:NATIVE": _FakeAsset()}, 0, now=1000)
    pending = lc.list_pending(db)
    assert len(pending) == 1 and pending[0]["status"] == lc.STATUS_PENDING
