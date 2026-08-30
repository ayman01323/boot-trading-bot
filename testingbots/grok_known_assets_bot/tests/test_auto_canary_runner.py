from __future__ import annotations

import sqlite3
from pathlib import Path

from grok_known_assets_bot import auto_canary_runner as auto
from grok_known_assets_bot import live_canary as lc
from grok_known_assets_bot.core import Journal, MarketSnapshot


class _Asset:
    key = "solana:SOL:NATIVE"


class _Risk:
    stop_min_pct = 2.5
    min_liquidity_usd = 250_000.0
    max_spread_bps = 80.0
    max_hold_minutes = 60.0
    take_profit_1_pct = 2.0
    take_profit_2_pct = 4.0


class _Envelope:
    def __init__(self, snapshot):
        self.snapshot = snapshot


class _Feed:
    risk = _Risk()

    def __init__(self, snapshot):
        self.snapshot = snapshot

    @staticmethod
    def supported(_asset):
        return True

    def collect(self, _asset, *, now):
        return _Envelope(self.snapshot)


def _db(tmp_path: Path) -> tuple[Journal, sqlite3.Connection]:
    journal = Journal(str(tmp_path / "state.sqlite3"))
    lc.ensure_schema(journal.db)
    return journal, journal.db


def _snapshot(*, reverse_bid=100.0, liquidity=1_000_000.0, spread=5.0, ret_1m=0.0):
    return MarketSnapshot(
        asset_key="solana:SOL:NATIVE",
        ts=2000.0,
        bid=100.0,
        ask=100.0,
        reverse_bid=reverse_bid,
        liquidity_usd=liquidity,
        volume_5m_usd=100_000.0,
        ret_1m_pct=ret_1m,
        ret_5m_pct=0.0,
        ret_15m_pct=0.0,
        vol_5m_pct=0.1,
        spread_bps=spread,
        price_impact_bps=0.0,
        fee_bps=0.0,
        sellable=True,
        slippage_bps=0.0,
    )


def _confirmed_entry(db: sqlite3.Connection, *, acquired=9_000_000, spend_micro=900_000, now=1000) -> str:
    aid = lc.create_pending_entry(
        db,
        asset_key="solana:SOL:NATIVE",
        mint="SOL",
        input_micro_usdc=spend_micro,
        target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=8_900_000,
        slippage_bps=50,
        evidence={"source": "test"},
        now=now,
    )
    lc.approve_entry(db, aid, user_id="u", chat_id="c", now=now + 1)
    lc.claim_next_approved(db, now=now + 2)
    lc.mark_broadcast_submitted(db, aid, now=now + 3)
    lc.mark_confirmed(db, aid, tx_signature="sig", acquired_lamports=acquired, now=now + 4)
    return aid


def test_hard_stop_generates_sell_reason():
    position = {"input_micro_usdc": 900_000, "acquired_lamports": 9_000_000, "updated_epoch": 1000}
    reason, net = auto._exit_reason(position, _snapshot(reverse_bid=97.0), _Risk(), now=1100)
    assert reason == "HARD_STOP"
    assert net < -2.5


def test_tp2_generates_sell_reason():
    position = {"input_micro_usdc": 900_000, "acquired_lamports": 9_000_000, "updated_epoch": 1000}
    reason, net = auto._exit_reason(position, _snapshot(reverse_bid=105.0), _Risk(), now=1100)
    assert reason == "TAKE_PROFIT_2"
    assert net >= 4.0


def test_tp1_is_review_prompt_not_partial_auto_exit():
    position = {"input_micro_usdc": 900_000, "acquired_lamports": 9_000_000, "updated_epoch": 1000}
    reason, net = auto._exit_reason(position, _snapshot(reverse_bid=102.5), _Risk(), now=1100)
    assert reason == "TAKE_PROFIT_1_REVIEW"
    assert net >= 2.0


def test_hold_generates_no_sell_reason():
    position = {"input_micro_usdc": 900_000, "acquired_lamports": 9_000_000, "updated_epoch": 1000}
    reason, _ = auto._exit_reason(position, _snapshot(reverse_bid=100.5), _Risk(), now=1100)
    assert reason is None


def test_auto_exit_signal_emits_approval_command_only(tmp_path):
    journal, db = _db(tmp_path)
    pid = _confirmed_entry(db)
    feed = _Feed(_snapshot(reverse_bid=97.0))

    count = auto.prepare_auto_exit_signals(
        journal,
        db,
        feed,
        {"solana:SOL:NATIVE": _Asset()},
        now=1100,
    )
    assert count == 1
    row = db.execute(
        "SELECT payload FROM events WHERE kind='CANARY_PENDING' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    import json
    payload = json.loads(row[0])
    assert payload["kind"] == "EXIT_SIGNAL"
    assert payload["position_approval_id"] == pid
    assert payload["approve_with"] == f"/grokexit {pid} CONFIRM"
    assert payload["automatic_broadcast"] is False
    # No EXIT approval row is created until the owner explicitly confirms it.
    exit_count = db.execute("SELECT COUNT(*) FROM live_canary_approvals WHERE kind='EXIT'").fetchone()[0]
    assert exit_count == 0


def test_auto_exit_signal_is_deduped(tmp_path):
    journal, db = _db(tmp_path)
    _confirmed_entry(db)
    feed = _Feed(_snapshot(reverse_bid=97.0))
    assets = {"solana:SOL:NATIVE": _Asset()}
    assert auto.prepare_auto_exit_signals(journal, db, feed, assets, now=1100) == 1
    assert auto.prepare_auto_exit_signals(journal, db, feed, assets, now=1110) == 0


def test_existing_exit_ticket_blocks_duplicate_signal(tmp_path):
    journal, db = _db(tmp_path)
    pid = _confirmed_entry(db)
    lc.create_approved_exit(db, position_approval_id=pid, user_id="u", chat_id="c", now=1100)
    feed = _Feed(_snapshot(reverse_bid=97.0))
    count = auto.prepare_auto_exit_signals(journal, db, feed, {"solana:SOL:NATIVE": _Asset()}, now=1110)
    assert count == 0
