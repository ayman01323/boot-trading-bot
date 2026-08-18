from __future__ import annotations

import inspect
import sqlite3
from types import SimpleNamespace

import pytest

from learnerbot import solana_execution_efficiency_patch as eff


def _cfg(**overrides):
    cfg = {key: value for key, (value, _description) in eff._EFFICIENCY_DEFAULTS.items()}
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


class _Executor:
    telegram_id = "123"
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()

    def _headers(self, json_body=False):
        return {}


def _valid_order(**overrides):
    order = {
        "transaction": "AA==",
        "requestId": "req-1",
        "router": "metis",
        "outAmount": "500000",
        "slippageBps": 50,
        "priceImpact": "0",
        "signatureFeeLamports": 5000,
        "prioritizationFeeLamports": 0,
        "rentFeeLamports": 0,
        "feeBps": 0,
        "routePlan": [{"swapInfo": {"label": "one-hop"}}],
    }
    order.update(overrides)
    return order


def test_tiny_live_trade_fee_cap_is_12500_lamports():
    # 0.0005 SOL = 500,000 lamports.  The default 3% ratio gives 15,000,
    # while 10% expected edge * 25% fee share gives the tighter 12,500 cap.
    assert eff.dynamic_fee_cap_lamports(_cfg(), 500_000) == 12_500


def test_old_1844400_lamport_priority_pattern_is_rejected(monkeypatch):
    monkeypatch.setattr(eff, "_guard_event", lambda *args, **kwargs: None)
    order = _valid_order(prioritizationFeeLamports=1_844_400)
    with pytest.raises(eff._exec.SolanaLiveError, match="exceeds dynamic cap"):
        eff._validate_order(
            _Executor(), order, eff._sol.WSOL_MINT, "mint", 500_000,
            trade_value_lamports=500_000,
            fee_cap_lamports=12_500,
            cfg=_cfg(),
        )


def test_combined_price_impact_and_slippage_rejected(monkeypatch):
    monkeypatch.setattr(eff, "_guard_event", lambda *args, **kwargs: None)
    # priceImpact 1.10 percentage points = 110 bps; + 50 bps slippage = 160 bps.
    order = _valid_order(priceImpact="1.10", slippageBps=50)
    with pytest.raises(eff._exec.SolanaLiveError, match="price impact"):
        eff._validate_order(
            _Executor(), order, eff._sol.WSOL_MINT, "mint", 500_000,
            trade_value_lamports=500_000,
            fee_cap_lamports=12_500,
            cfg=_cfg(),
        )


def test_multihop_route_uses_stricter_100_bps_guard(monkeypatch):
    monkeypatch.setattr(eff, "_guard_event", lambda *args, **kwargs: None)
    order = _valid_order(
        priceImpact="0.60",  # 60 bps
        slippageBps=50,       # total 110 bps
        routePlan=[{"swapInfo": {"label": "hop1"}}, {"swapInfo": {"label": "hop2"}}],
    )
    with pytest.raises(eff._exec.SolanaLiveError, match="exceeds 100 bps"):
        eff._validate_order(
            _Executor(), order, eff._sol.WSOL_MINT, "mint", 500_000,
            trade_value_lamports=500_000,
            fee_cap_lamports=12_500,
            cfg=_cfg(),
        )


def test_managed_order_default_has_no_jito_tip_and_bounded_priority(monkeypatch):
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return _valid_order()

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response()

    monkeypatch.setattr(eff, "_cfg", lambda app: _cfg())
    monkeypatch.setattr(eff.requests, "get", fake_get)
    result = eff.order_with_economic_caps(_Executor(), eff._sol.WSOL_MINT, "mint", 500_000)
    assert calls
    params = calls[-1]
    assert "jitoTipLamports" not in params
    assert int(params["priorityFeeLamports"]) == 7_500
    assert result["_fee_cap_lamports"] == 12_500
    assert result["_requested_jito_tip_lamports"] == 0


def test_atomic_candidate_requires_exact_tracked_bot_created_source_account(tmp_path, monkeypatch):
    db_path = tmp_path / "solana.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE positions(
             position_id TEXT, telegram_id TEXT, mint TEXT, mode TEXT, status TEXT, entry_ts INTEGER
           )"""
    )
    conn.execute(
        "INSERT INTO positions VALUES('p1','123','mint','LIVE','OPEN',1)"
    )
    conn.commit()
    conn.close()

    def connect(_app):
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    executor = _Executor()
    executor.token_balance_raw = lambda mint: 42
    monkeypatch.setattr(eff._sol, "connect", connect)
    monkeypatch.setattr(
        eff._binding,
        "_binding",
        lambda app, pid: {"position_id": pid, "wallet_address": executor.address},
    )
    live = {
        "pubkey": "TokenAccount111111111111111111111111111111",
        "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "amount": 42,
        "lamports": 2_039_280,
    }
    monkeypatch.setattr(eff._reclaim, "_token_accounts", lambda ex, mint: {live["pubkey"]: live})
    monkeypatch.setattr(
        eff._reclaim,
        "_tracked_accounts",
        lambda app, pid: [{
            "position_id": pid,
            "account_pubkey": live["pubkey"],
            "program_id": live["program_id"],
            "entry_lamports": "2039280",
        }],
    )
    position, tracked, resolved = eff._atomic_candidate(executor, "mint", 42)
    assert position["position_id"] == "p1"
    assert tracked["account_pubkey"] == live["pubkey"]
    assert resolved["amount"] == 42

    monkeypatch.setattr(eff._reclaim, "_tracked_accounts", lambda app, pid: [])
    with pytest.raises(eff._exec.SolanaLiveError, match="not proven to have been created by this bot"):
        eff._atomic_candidate(executor, "mint", 42)


def test_receipt_schema_has_requested_cost_breakdown_fields():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    eff._ensure(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_execution_receipts)").fetchall()}
    required = {
        "raw_input_amount", "raw_output_amount", "network_fee_lamports", "base_fee_lamports",
        "priority_fee_lamports", "jito_tip_lamports", "platform_fee_amount",
        "rent_paid_lamports", "rent_reclaimed_lamports", "net_execution_pnl_lamports",
        "slippage_bps", "price_impact_bps", "atomic_token_account_close",
        "reused_existing_output_token_account",
    }
    assert required.issubset(columns)


def test_atomic_path_uses_same_transaction_close_and_not_tx_jup_submission():
    source = inspect.getsource(eff.atomic_full_sell)
    assert "close_ix" in source
    assert "bytes([9])" in source
    assert "sendTransaction" in source
    assert "tx.jup.ag" not in source
    # CloseAccount destination and owner are both the signing wallet.
    assert source.count("AccountMeta(owner_pk") >= 2


def test_execution_efficiency_defaults_never_enable_unbounded_tip():
    assert eff._EFFICIENCY_DEFAULTS["live_enable_jito_tip"][0] == "false"
    assert int(eff._EFFICIENCY_DEFAULTS["live_max_jito_tip_lamports"][0]) == 1000
    assert int(eff._EFFICIENCY_DEFAULTS["live_max_total_fee_lamports"][0]) == 100000
