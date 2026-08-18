from types import SimpleNamespace

import pytest

from learnerbot import solana_live_executor as live_exec
from learnerbot import solana_simulated_reserve_guard_patch as reserve


def _executor():
    ex = live_exec.SolanaLiveExecutor.__new__(live_exec.SolanaLiveExecutor)
    ex.app = object()
    ex.address = "wallet"
    return ex


def test_buy_simulation_blocks_post_balance_below_reserve(monkeypatch):
    ex = _executor()
    ex._minimum_post_buy_reserve_lamports = 5_000_000
    monkeypatch.setattr(
        reserve._sol,
        "_rpc",
        lambda *args: {"value": {"err": None, "accounts": [{"lamports": 4_900_000}] }},
    )
    with pytest.raises(live_exec.SolanaLiveError, match="below untouched reserve"):
        reserve._simulate_with_wallet_snapshot(ex, "signed")


def test_buy_simulation_accepts_post_balance_at_reserve(monkeypatch):
    ex = _executor()
    ex._minimum_post_buy_reserve_lamports = 5_000_000
    monkeypatch.setattr(
        reserve._sol,
        "_rpc",
        lambda *args: {"value": {"err": None, "accounts": [{"lamports": 5_000_000}] }},
    )
    result = reserve._simulate_with_wallet_snapshot(ex, "signed")
    assert result["simulated_post_wallet_lamports"] == 5_000_000


def test_non_buy_simulation_does_not_require_wallet_snapshot(monkeypatch):
    ex = _executor()
    monkeypatch.setattr(
        reserve._sol,
        "_rpc",
        lambda *args: {"value": {"err": None, "accounts": None}},
    )
    result = reserve._simulate_with_wallet_snapshot(ex, "signed")
    assert result["err"] is None
