from __future__ import annotations
from .db import get_state,set_state

def _int(v,default=0):
    if v in (None,''):return default
    if isinstance(v,int):return v
    return int(v,16) if isinstance(v,str) and v.startswith('0x') else int(v)
def selector(input_data):
    if not input_data or len(input_data)<10:return '0x'
    return input_data[:10].lower()
def ingest_block(conn,rpc,block_number):
    block=rpc.block(block_number,True);timestamp=_int(block.get('timestamp'));block_hash=(block.get('hash') or '').lower()
    conn.execute('INSERT OR REPLACE INTO blocks(number,block_hash,timestamp) VALUES(?,?,?)',(block_number,block_hash,timestamp));count=0
    for tx in block.get('transactions',[]):
        frm=(tx.get('from') or '').lower()
        if not frm:continue
        to=(tx.get('to') or '').lower() or None; inp=tx.get('input') or '0x'
        conn.execute("""INSERT OR IGNORE INTO transactions(tx_hash,block_number,tx_index,from_addr,to_addr,selector,value_wei,gas_limit,gas_price_wei,nonce,input_len) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",((tx.get('hash') or '').lower(),block_number,_int(tx.get('transactionIndex'),None) if tx.get('transactionIndex') is not None else None,frm,to,selector(inp),str(_int(tx.get('value'))),_int(tx.get('gas')),str(_int(tx.get('gasPrice'))),_int(tx.get('nonce')),max(0,(len(inp)-2)//2)));count+=1
    conn.commit();set_state(conn,'last_scanned_block',str(block_number));return count

def scan(conn,rpc,blocks:int|None,finality_lag:int,max_blocks:int|None=None):
    latest=rpc.latest_block();chain_target=max(0,latest-finality_lag);last=get_state(conn,'last_scanned_block')
    if blocks is not None:
        # Manual historical scan: inspect exactly the latest N finalised blocks.
        start=max(0,chain_target-int(blocks)+1);target=chain_target
    elif last is not None:
        start=int(last)+1;target=chain_target
        if max_blocks is not None and max_blocks>0:target=min(target,start+int(max_blocks)-1)
    else:
        n=int(max_blocks or 100);start=max(0,chain_target-n+1);target=chain_target
    if start>target:return (0,start,target)
    txs=0
    for n in range(start,target+1):txs+=ingest_block(conn,rpc,n)
    return txs,start,target
