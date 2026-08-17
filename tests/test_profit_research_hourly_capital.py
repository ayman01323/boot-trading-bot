from pathlib import Path
from types import SimpleNamespace

from learnerbot import hourly_capital_alert_patch as alerts
from learnerbot import profit_research_expansion_patch as broad
from learnerbot.db import connect


def test_broad_profit_research_rotates_candidate_batches(tmp_path, monkeypatch):
    db = tmp_path / "chain.sqlite3"
    conn = connect(db)
    for i in range(120):
        wallet = "0x" + f"{i+1:040x}"
        txh = "0x" + f"{i+1000:064x}"
        conn.execute(
            "INSERT INTO wallet_scores(wallet,tx_count,bot_score,primary_executor) VALUES(?,?,?,?)",
            (wallet, 1, float(120 - i), None),
        )
        conn.execute(
            "INSERT INTO blocks(number,block_hash,timestamp) VALUES(?,?,?)",
            (i + 1, f"b{i}", 1_800_000_000 + i),
        )
        conn.execute(
            """INSERT INTO transactions(tx_hash,block_number,tx_index,from_addr,to_addr,selector,value_wei,receipt_scanned)
               VALUES(?,?,?,?,?,?,?,1)""",
            (txh, i + 1, 0, wallet, None, None, "0"),
        )
    conn.commit()

    seen = []
    monkeypatch.setattr(broad, "analyse_tx", lambda conn, settings, wallet, tx_hash, executor: seen.append(wallet))

    app = SimpleNamespace(general=lambda: {
        "profit_research_candidate_pool": "100",
        "profit_research_batch_per_cycle": "50",
        "profit_research_txs_per_wallet": "1",
    })
    settings = SimpleNamespace(app=app, csv_dir=tmp_path, chain_id=56)

    first = broad.analyse_top_wallets(conn, object(), settings, 20)
    first_seen = set(seen)
    seen.clear()
    second = broad.analyse_top_wallets(conn, object(), settings, 20)
    second_seen = set(seen)

    assert first["candidate_pool"] == 100
    assert first["wallets"] == 50
    assert second["wallets"] == 50
    assert first_seen
    assert second_seen
    assert first_seen.isdisjoint(second_seen)
    conn.close()


def test_hourly_alert_warns_low_gas_and_profit_without_capital(tmp_path, monkeypatch):
    chain = SimpleNamespace(chain_id=56, slug="bsc", name="BSC", native_symbol="BNB")
    data = {
        "capital_usd": 12,
        "wallets": [{
            "active": True,
            "address": "0x" + "1" * 40,
            "chains": [{
                "chain_id": 56,
                "native_balance": "0.001",
                "capital_usd": "12",
            }],
        }],
    }
    monkeypatch.setattr(alerts, "user_dashboard_data", lambda app, tid: data)
    monkeypatch.setattr(alerts, "load_chains", lambda app, enabled_only=True: [chain])
    monkeypatch.setattr(alerts, "_reserve_for", lambda app, tid, chain: alerts.Decimal("0.005"))
    monkeypatch.setattr(alerts, "_min_trade_for", lambda app, tid, chain: alerts.Decimal("0.0001"))
    monkeypatch.setattr(alerts._sibot, "ranking_rows", lambda app, tid, cid: [{"wallet": "0xabc"}] * 7)
    monkeypatch.setattr(alerts._sol, "ranking_rows", lambda app, tid: [])

    app = SimpleNamespace(csv_dir=Path(tmp_path))
    text = alerts.build_hourly_capital_alert(app, "123")

    assert "GAS BELOW RESERVE" in text
    assert "Positive-profit wallets found  <b>7</b>" in text
    assert "PROFIT EVIDENCE BUT NO USABLE CAPITAL" in text
    assert "below 0.0001 BNB" in text
