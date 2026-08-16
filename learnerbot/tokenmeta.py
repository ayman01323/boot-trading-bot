from __future__ import annotations
import time
from .config import token_override
from .rpc import RPCClient
SYMBOL_SELECTOR='0x95d89b41'; DECIMALS_SELECTOR='0x313ce567'
def _decode_symbol(data):
    if not data or data=='0x':return ''
    raw=bytes.fromhex(data[2:])
    if len(raw)==32:return raw.rstrip(b'\x00').decode('utf-8',errors='replace').strip()
    if len(raw)>=64:
        try:
            off=int.from_bytes(raw[:32],'big'); length=int.from_bytes(raw[off:off+32],'big');return raw[off+32:off+32+length].decode('utf-8',errors='replace').strip()
        except:return ''
    return ''
def get_token_meta(conn,rpc:RPCClient,token:str,csv_dir=None,chain_id=None):
    token=token.lower()
    if csv_dir is not None and chain_id is not None:
        ov=token_override(csv_dir,chain_id,token)
        if ov:
            symbol,decimals=ov;conn.execute("INSERT INTO token_meta(token,symbol,decimals,updated_at) VALUES(?,?,?,?) ON CONFLICT(token) DO UPDATE SET symbol=excluded.symbol,decimals=excluded.decimals,updated_at=excluded.updated_at",(token,symbol[:64],decimals,int(time.time())));conn.commit();return symbol[:64],decimals
    row=conn.execute('SELECT symbol,decimals FROM token_meta WHERE token=?',(token,)).fetchone()
    if row and row['decimals'] is not None:return (row['symbol'] or token[:8],int(row['decimals']))
    symbol='';decimals=18
    try:symbol=_decode_symbol(rpc.eth_call(token,SYMBOL_SELECTOR))
    except:pass
    try:
        raw=rpc.eth_call(token,DECIMALS_SELECTOR)
        if raw and raw!='0x':
            decimals=int(raw,16)
            if decimals<0 or decimals>36:decimals=18
    except:pass
    if not symbol:symbol=token[:10]
    conn.execute("INSERT INTO token_meta(token,symbol,decimals,updated_at) VALUES(?,?,?,?) ON CONFLICT(token) DO UPDATE SET symbol=excluded.symbol,decimals=excluded.decimals,updated_at=excluded.updated_at",(token,symbol[:64],decimals,int(time.time())));conn.commit();return symbol[:64],decimals
