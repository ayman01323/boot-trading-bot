from pathlib import Path
from tempfile import TemporaryDirectory
from learnerbot.db import connect
from learnerbot.profit import analyse_tx
class Settings:
    native_usd=600.0; wrapped_base_symbol='WBNB'; native_symbol='BNB'; chain_id=56

def test_wrapped_base_delta_minus_gas():
    with TemporaryDirectory() as td:
        td=Path(td); Settings.csv_dir=td
        (td/'builders.csv').write_text('chain_id,name,address,enabled\n')
        conn=connect(td/'test.sqlite3');wallet='0x'+'1'*40;executor='0x'+'2'*40;pool='0x'+'3'*40;token='0x'+'4'*40;txh='0x'+'a'*64
        conn.execute("INSERT INTO blocks(number,block_hash,timestamp) VALUES(1,'h',1000)")
        conn.execute("""INSERT INTO transactions(tx_hash,block_number,tx_index,from_addr,to_addr,selector,value_wei,gas_limit,gas_price_wei,nonce,input_len,status,gas_used,effective_gas_price_wei,receipt_scanned) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(txh,1,0,wallet,executor,'0x12345678','0',100000,'1000000000',1,64,1,100000,'1000000000',1))
        conn.execute("INSERT INTO token_meta(token,symbol,decimals,updated_at) VALUES(?,?,?,0)",(token,'WBNB',18))
        conn.execute("INSERT INTO token_transfers(tx_hash,log_index,token,from_addr,to_addr,raw_amount) VALUES(?,?,?,?,?,?)",(txh,0,token,executor,pool,str(10*10**18)))
        conn.execute("INSERT INTO token_transfers(tx_hash,log_index,token,from_addr,to_addr,raw_amount) VALUES(?,?,?,?,?,?)",(txh,1,token,pool,executor,str(int(10.02*10**18))))
        conn.commit();out=analyse_tx(conn,Settings,wallet,txh,executor)
        assert out['proof_quality']=='PROVEN_WRAPPED_BASE';assert abs(out['net_base']-0.0199)<1e-9
