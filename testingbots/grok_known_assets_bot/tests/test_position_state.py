from __future__ import annotations

from pathlib import Path

from grok_known_assets_bot.core import Asset, Journal, Position
from grok_known_assets_bot.position_state import restore_positions, sync_positions


def _asset() -> Asset:
    return Asset(
        key="solana:SOL:NATIVE",
        chain="solana",
        symbol="SOL",
        address="NATIVE",
        enabled=True,
    )


def _position() -> Position:
    return Position(
        asset_key="solana:SOL:NATIVE",
        chain="solana",
        opened_ts=1000.0,
        entry_price=100.0,
        quantity=2.0,
        remaining_quantity=2.0,
        stop_pct=3.0,
        peak_net_pct=1.2,
        took_tp1=False,
        trade_id="trade-1",
        entry_execution_cost_bps=12.0,
    )


def test_position_survives_journal_restart(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"
    first = Journal(db_path)
    p = _position()
    sync_positions(first, {p.asset_key: p})
    first.db.close()

    second = Journal(db_path)
    restored = restore_positions(second, {p.asset_key: _asset()})
    assert set(restored) == {p.asset_key}
    recovered = restored[p.asset_key]
    assert recovered.trade_id == "trade-1"
    assert recovered.stop_pct == 3.0
    assert recovered.remaining_quantity == 2.0
    assert recovered.entry_execution_cost_bps == 12.0


def test_partial_position_state_is_updated(tmp_path: Path):
    journal = Journal(tmp_path / "state.sqlite3")
    p = _position()
    sync_positions(journal, {p.asset_key: p})
    p.remaining_quantity = 1.0
    p.took_tp1 = True
    p.peak_net_pct = 2.4
    sync_positions(journal, {p.asset_key: p})

    restored = restore_positions(journal, {p.asset_key: _asset()})
    recovered = restored[p.asset_key]
    assert recovered.remaining_quantity == 1.0
    assert recovered.took_tp1 is True
    assert recovered.peak_net_pct == 2.4


def test_final_close_removes_persisted_position(tmp_path: Path):
    journal = Journal(tmp_path / "state.sqlite3")
    p = _position()
    sync_positions(journal, {p.asset_key: p})
    sync_positions(journal, {})
    assert restore_positions(journal, {p.asset_key: _asset()}) == {}


def test_invalid_position_fails_closed_and_is_removed(tmp_path: Path):
    journal = Journal(tmp_path / "state.sqlite3")
    journal.set_state(
        "paper_position:solana:SOL:NATIVE",
        {
            "paper": True,
            "asset_key": "solana:SOL:NATIVE",
            "chain": "solana",
            "opened_ts": 1000,
            "entry_price": 100,
            "quantity": 2,
            "remaining_quantity": 3,
            "stop_pct": 3,
        },
    )
    assert restore_positions(journal, {"solana:SOL:NATIVE": _asset()}) == {}
    assert journal.get_state("paper_position:solana:SOL:NATIVE") is None
