from __future__ import annotations

from pathlib import Path

from grok_known_assets_bot import control, live_canary as lc
from grok_known_assets_bot import live_canary_runner as runner
from grok_known_assets_bot import live_execution as lx
from grok_known_assets_bot.core import Journal


class _BalanceExecutor:
    def __init__(self, lamports: int) -> None:
        self._lamports = int(lamports)

    def native_balance_lamports(self) -> int:
        return self._lamports


def test_exit_balance_requires_position_plus_fee_reserve():
    need = lc.TARGET_LAMPORTS
    ok, reason = lx.preflight_exit_balance(
        need_sol_lamports=need,
        executor=_BalanceExecutor(need + lx.SOL_FEE_RESERVE_LAMPORTS - 1),
    )
    assert ok is False
    assert "insufficient on-chain SOL" in reason

    ok, reason = lx.preflight_exit_balance(
        need_sol_lamports=need,
        executor=_BalanceExecutor(need + lx.SOL_FEE_RESERVE_LAMPORTS),
    )
    assert ok is True
    assert reason == "ok"


def _seed_executing_exit(db, *, now: int = 1000) -> dict:
    entry_id = lc.create_pending_entry(
        db,
        asset_key="solana:SOL:NATIVE",
        mint=runner.SOL_MINT,
        input_micro_usdc=1_000_000,
        target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=lc.TARGET_LAMPORTS - 50_000,
        slippage_bps=50,
        evidence={"route_id": "poolA"},
        now=now,
    )
    lc.approve_entry(db, entry_id, user_id="u", chat_id="c", now=now + 1)
    lc.claim_next_approved(db, now=now + 2)
    lc.mark_broadcast_submitted(db, entry_id, now=now + 3)
    lc.mark_confirmed(
        db,
        entry_id,
        tx_signature="ENTRY_SIG",
        acquired_lamports=lc.TARGET_LAMPORTS,
        now=now + 4,
    )
    exit_id = lc.create_approved_exit(
        db,
        position_approval_id=entry_id,
        user_id="u",
        chat_id="c",
        now=now + 5,
    )
    ticket = lc.claim_next_approved(db, now=now + 6)
    assert ticket is not None and ticket["approval_id"] == exit_id
    return ticket


def test_runner_refuses_exit_before_swap_when_onchain_balance_not_proven(tmp_path: Path, monkeypatch):
    control_file = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(control_file))
    control.save_state(
        armed=True,
        live_readiness_enabled=True,
        live_canary_enabled=True,
        updated_by="test",
    )

    journal = Journal(str(tmp_path / "state.sqlite3"))
    db = journal.db
    lc.ensure_schema(db)
    ticket = _seed_executing_exit(db)

    monkeypatch.setattr(runner, "_revalidate_exit", lambda *a, **k: (True, "ok", 1_000_000))
    monkeypatch.setattr(
        lx,
        "preflight_exit_funding",
        lambda **_kwargs: (False, "insufficient on-chain SOL for approved exit"),
    )
    swap_calls: list[dict] = []
    monkeypatch.setattr(lx, "execute_swap", lambda **kwargs: swap_calls.append(kwargs))

    runner._execute_ticket(journal, db, ticket, feed=None, assets={}, now=1050)

    row = lc._row(db, ticket["approval_id"])
    assert row is not None
    assert row["status"] == lc.STATUS_REJECTED_REVALIDATION
    assert swap_calls == []
