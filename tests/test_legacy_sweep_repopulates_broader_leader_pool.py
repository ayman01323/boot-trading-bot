"""End-to-end regression for legacy Etherscan backlog -> Alchemy history -> leader pool.

This preserves Claude's useful integration coverage on top of current main. It
exercises the real legacy-sweep selector and the real broader candidate query
against SQLite without changing any trading threshold or runtime behaviour.
"""

import time
from contextlib import closing
from types import SimpleNamespace

from learnerbot import sibot
from learnerbot import sibot_broader_qualified_leader_patch as broader
from learnerbot import sibot_legacy_error_sweep_patch as sweep


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=csv_dir)


def _chain(slug="bsc", chain_id=56):
    return SimpleNamespace(slug=slug, chain_id=chain_id)


def _insert_errored_wallet(app, chain, wallet, fetched_at):
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,history_complete,error
               ) VALUES(?,?,?,?,0,?)""",
            (
                chain.chain_id,
                chain.slug,
                wallet,
                fetched_at,
                "RuntimeError: Etherscan txlist: NOTOK Free API access is not supported for this chain",
            ),
        )
        conn.commit()


def _apply_successful_refresh(app, chain, wallet, *, net_native="0.05"):
    """Apply the DB contract produced by a successful history reconstruction."""
    now = int(time.time())
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO wallet_trades(
                   trade_id,chain_id,chain_slug,wallet,token,symbol,decimals,
                   buy_tx,sell_tx,buy_ts,sell_ts,token_amount_raw,cost_native,
                   proceeds_native,buy_gas_native,sell_gas_native,net_native,
                   source,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"trade-{wallet}",
                chain.chain_id,
                chain.slug,
                wallet.lower(),
                "0xtoken",
                "TOK",
                18,
                "0xbuy",
                "0xsell",
                now - 3600,
                now,
                "1000000000000000000",
                "0.04",
                "0.09",
                "0.001",
                "0.001",
                net_native,
                "reconstructed",
                now,
            ),
        )
        conn.execute(
            """INSERT INTO wallet_history_status(chain_id,chain_slug,wallet,fetched_at,history_complete,error)
               VALUES(?,?,?,?,1,'')
               ON CONFLICT(chain_id,wallet) DO UPDATE SET
                   fetched_at=excluded.fetched_at,history_complete=1,error=''""",
            (chain.chain_id, chain.slug, wallet.lower(), now),
        )
        conn.commit()


def test_legacy_sweep_selection_flows_through_to_broader_leader_pool(monkeypatch, tmp_path):
    app = _app(tmp_path)
    chain = _chain()
    wallet = "0x" + "ab" * 20
    monkeypatch.setattr(sweep, "_sweep_seconds", lambda app, chain: 900)
    _insert_errored_wallet(app, chain, wallet, fetched_at=100)

    assert sweep._next_legacy_error_wallet(app, chain, now_epoch=10_000) == wallet
    cfg = {"lookback_days": "60", "leader_selection_candidate_cap": "500"}
    assert broader._broad_candidates(app, chain.chain_id, cfg) == []

    _apply_successful_refresh(app, chain, wallet)

    assert sweep._next_legacy_error_wallet(app, chain, now_epoch=20_000) is None
    assert broader._broad_candidates(app, chain.chain_id, cfg) == [wallet.lower()]


def test_unprofitable_reconstructed_trade_does_not_enter_the_pool(tmp_path):
    app = _app(tmp_path)
    chain = _chain()
    wallet = "0x" + "cd" * 20
    _apply_successful_refresh(app, chain, wallet, net_native="-0.02")

    cfg = {"lookback_days": "60", "leader_selection_candidate_cap": "500"}
    assert broader._broad_candidates(app, chain.chain_id, cfg) == []
