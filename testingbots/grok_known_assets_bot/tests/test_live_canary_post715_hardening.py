from __future__ import annotations

from pathlib import Path

from grok_known_assets_bot import control, live_canary as lc
from grok_known_assets_bot import live_canary_runner as runner
from grok_known_assets_bot import live_execution as lx
from grok_known_assets_bot.core import Journal
from grok_known_assets_bot.live_readiness import ENTRY_TARGET_SOL, HARD_MAX_ENTRY_SOL


def _journal(tmp_path: Path) -> Journal:
    j = Journal(str(tmp_path / "state.sqlite3"))
    lc.ensure_schema(j.db)
    return j


def _control_on(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_CONTROL_FILE", str(tmp_path / "grok_control.json"))
    control.save_state(
        armed=True,
        live_readiness_enabled=True,
        live_canary_enabled=True,
        updated_by="test",
    )


def _confirmed_entry(db, *, now: int = 1000, acquired: int = 8_800_000) -> str:
    approval_id = lc.create_pending_entry(
        db,
        asset_key="solana:SOL:NATIVE",
        mint=runner.SOL_MINT,
        input_micro_usdc=2_000_000,
        target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=acquired,
        slippage_bps=50,
        evidence={"route_id": "entry-route"},
        now=now,
    )
    lc.approve_entry(db, approval_id, user_id="u", chat_id="c", now=now + 1)
    assert lc.claim_next_approved(db, now=now + 2)["approval_id"] == approval_id
    assert lc.mark_broadcast_submitted(db, approval_id, now=now + 3)
    assert lc.mark_confirmed(
        db,
        approval_id,
        tx_signature="ENTRY_SIG",
        acquired_lamports=acquired,
        now=now + 4,
    )
    return approval_id


class _FakeSettings:
    slippage_bps = 50
    request_timeout_seconds = 2.0


class _FakeFeed:
    settings = _FakeSettings()


class _BalanceExecutor:
    def __init__(self, lamports: int):
        self._lamports = int(lamports)

    def native_balance_lamports(self) -> int:
        return self._lamports


def test_readiness_values_are_derived_from_integer_canary_limits():
    assert ENTRY_TARGET_SOL == lc.TARGET_LAMPORTS / 1_000_000_000
    assert HARD_MAX_ENTRY_SOL == lc.HARD_CAP_LAMPORTS / 1_000_000_000


def test_exit_balance_requires_position_plus_fee_reserve():
    need = 8_800_000
    ok, _ = lx.preflight_exit_balance(
        need_sol_lamports=need,
        executor=_BalanceExecutor(need + lc.SOL_FEE_RESERVE_LAMPORTS),
    )
    assert ok is True

    ok, reason = lx.preflight_exit_balance(
        need_sol_lamports=need,
        executor=_BalanceExecutor(need + lc.SOL_FEE_RESERVE_LAMPORTS - 1),
    )
    assert ok is False
    assert "insufficient on-chain SOL" in reason


def test_exit_rejected_before_swap_when_live_balance_is_insufficient(tmp_path, monkeypatch):
    _control_on(tmp_path, monkeypatch)
    j = _journal(tmp_path)
    position_id = _confirmed_entry(j.db)
    exit_id = lc.create_approved_exit(
        j.db,
        position_approval_id=position_id,
        user_id="u",
        chat_id="c",
        now=1100,
    )
    ticket = lc.claim_next_approved(j.db, now=1101)
    assert ticket["approval_id"] == exit_id

    monkeypatch.setattr(runner, "_revalidate_exit", lambda *a, **k: (True, "ok", 1_500_000))
    monkeypatch.setattr(
        lx,
        "preflight_exit_funding",
        lambda **k: (False, "insufficient on-chain SOL for approved exit"),
    )
    called = []
    monkeypatch.setattr(lx, "execute_swap", lambda **k: called.append(k))

    runner._execute_ticket(j, j.db, ticket, _FakeFeed(), {}, now=1102)
    assert called == []
    row = lc._row(j.db, exit_id)
    assert row["status"] == lc.STATUS_REJECTED_REVALIDATION
    assert "insufficient on-chain SOL" in str(row["outcome_detail"])


def test_exit_uses_recorded_position_quantity_and_fresh_min_out(tmp_path, monkeypatch):
    _control_on(tmp_path, monkeypatch)
    j = _journal(tmp_path)
    acquired = 8_750_000
    position_id = _confirmed_entry(j.db, acquired=acquired)
    exit_id = lc.create_approved_exit(
        j.db,
        position_approval_id=position_id,
        user_id="u",
        chat_id="c",
        now=1100,
    )
    ticket = lc.claim_next_approved(j.db, now=1101)

    fresh_min_out = 1_650_000
    monkeypatch.setattr(runner, "_revalidate_exit", lambda *a, **k: (True, "ok", fresh_min_out))
    monkeypatch.setattr(lx, "preflight_exit_funding", lambda **k: (True, "ok"))
    seen = {}

    def fake_swap(**kwargs):
        seen.update(kwargs)
        kwargs["on_broadcast_submitted"]()
        return {"signature": "EXIT_SIG", "out_raw": 1_700_000, "wallet_delta_lamports": -acquired}

    monkeypatch.setattr(lx, "execute_swap", fake_swap)
    runner._execute_ticket(j, j.db, ticket, _FakeFeed(), {}, now=1102)

    assert seen["amount_raw"] == acquired
    assert seen["min_out_raw"] == fresh_min_out
    assert lc._row(j.db, exit_id)["status"] == lc.STATUS_CONFIRMED
    assert lc.open_entry_position_count(j.db) == 0


def test_claimed_ticket_cannot_execute_after_approval_ttl(tmp_path, monkeypatch):
    _control_on(tmp_path, monkeypatch)
    j = _journal(tmp_path)
    approval_id = lc.create_pending_entry(
        j.db,
        asset_key="solana:SOL:NATIVE",
        mint=runner.SOL_MINT,
        input_micro_usdc=2_000_000,
        target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=8_000_000,
        slippage_bps=50,
        evidence={},
        now=1000,
        ttl_seconds=30,
    )
    lc.approve_entry(j.db, approval_id, user_id="u", chat_id="c", now=1001)
    ticket = lc.claim_next_approved(j.db, now=1002)
    called = []
    monkeypatch.setattr(lx, "execute_swap", lambda **k: called.append(k))

    runner._execute_ticket(j, j.db, ticket, _FakeFeed(), {}, now=1031)
    assert called == []
    assert lc._row(j.db, approval_id)["status"] == lc.STATUS_EXPIRED
