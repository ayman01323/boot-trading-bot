import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from learnerbot.db import connect
from learnerbot.scoring import compute_scores

def test_repetitive_fast_wallet_scores_higher():
    with TemporaryDirectory() as td:
        td = Path(td)
        conn = connect(td / "test.sqlite3")
        csv_dir = td / "csv"
        csv_dir.mkdir()
        (csv_dir / "scoring_weights.csv").write_text(
            "feature,weight\n"
            "tx_count,20\n"
            "tx_rate,25\n"
            "repeat_to,20\n"
            "repeat_selector,20\n"
            "zero_value,10\n"
            "builder,5\n"
        )
        (csv_dir / "builders.csv").write_text("name,address,enabled\n")
        (csv_dir / "wallet_blocklist.csv").write_text("name,address,enabled\n")
        for i in range(20):
            conn.execute("INSERT INTO blocks(number,block_hash,timestamp) VALUES(?,?,?)", (i+1, f'h{i}', 1000+i*3))
            conn.execute(
                """INSERT INTO transactions(
                    tx_hash,block_number,tx_index,from_addr,to_addr,selector,value_wei,
                    gas_limit,gas_price_wei,nonce,input_len
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (f"0x{i:064x}",i+1,0,"0x"+"1"*40,"0x"+"2"*40,"0x12345678","0",100000,"1",i,64),
            )
        conn.commit()
        compute_scores(conn, csv_dir)
        row = conn.execute("SELECT bot_score FROM wallet_scores WHERE wallet=?", ("0x"+"1"*40,)).fetchone()
        assert row["bot_score"] > 60
