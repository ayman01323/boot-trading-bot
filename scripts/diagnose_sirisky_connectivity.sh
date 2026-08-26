#!/usr/bin/env bash
set -euo pipefail

TARGET=/root/SiRisky
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo 'ERROR: run via sudo bash'; exit 2; }
[[ -d "$TARGET" ]] || { echo 'ERROR: /root/SiRisky not found'; exit 3; }
cd "$TARGET"

PY=.venv/bin/python
[[ -x "$PY" ]] || { echo 'ERROR: SiRisky venv missing'; exit 4; }

"$PY" - <<'PY'
from pathlib import Path
from urllib.parse import urlparse
import csv, json, requests

def load_env():
    env={}
    p=Path('.env')
    if not p.is_file(): return env
    for raw in p.read_text(encoding='utf-8', errors='replace').splitlines():
        s=raw.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1)
        env[k.strip()]=v.strip().strip('"').strip("'")
    return env

def safe_json(resp):
    try: return resp.json()
    except Exception: return None

env=load_env()
print('=== SIRISKY CONNECTIVITY DIAGNOSTIC ===')

rpc=env.get('SOLANA_RPC_URL','')
print('rpc_present=' + str(bool(rpc)).lower())
print('rpc_host=' + (urlparse(rpc).hostname or 'UNKNOWN' if rpc else 'MISSING'))
if rpc:
    try:
        r=requests.post(rpc, json={'jsonrpc':'2.0','id':1,'method':'getLatestBlockhash'}, timeout=15)
        print('rpc_http_status=' + str(r.status_code))
        j=safe_json(r)
        if isinstance(j,dict) and 'result' in j:
            print('rpc_result=PASS')
        elif isinstance(j,dict) and 'error' in j:
            e=j.get('error') or {}
            print('rpc_result=FAIL')
            print('rpc_error_code=' + str(e.get('code','')))
            print('rpc_error_message=' + str(e.get('message',''))[:300])
        else:
            print('rpc_result=FAIL_NON_JSON_OR_UNEXPECTED')
    except Exception as e:
        print('rpc_result=FAIL_' + type(e).__name__)

wallet=''
try:
    rows=list(csv.DictReader(open('CSV/wallets.csv', encoding='utf-8-sig', newline='')))
    if rows: wallet=(rows[0].get('address') or '').strip()
except Exception:
    pass
print('wallet_address_present=' + str(bool(wallet)).lower())

key=env.get('JUPITER_API_KEY','')
print('jupiter_key_present=' + str(bool(key)).lower())
headers={'User-Agent':'SiRisky-connectivity-diagnostic'}
if key: headers['x-api-key']=key

# Test legacy quote endpoint because it gives a clear HTTP/auth signal.
try:
    r=requests.get('https://api.jup.ag/swap/v1/quote', params={
        'inputMint':'So11111111111111111111111111111111111111112',
        'outputMint':'EPjFWdd5AufqSSqeM2qPZo2mrQSSrSLRYhNACTM1v',
        'amount':'1000000','slippageBps':'100'
    }, headers=headers, timeout=20)
    print('jupiter_v1_http_status=' + str(r.status_code))
    j=safe_json(r)
    if isinstance(j,dict) and j.get('outAmount'):
        print('jupiter_v1_result=PASS')
    else:
        print('jupiter_v1_result=FAIL')
        if isinstance(j,dict):
            msg=j.get('error') or j.get('errorMessage') or j.get('message') or j.get('errorCode') or ''
            print('jupiter_v1_error=' + str(msg)[:300])
except Exception as e:
    print('jupiter_v1_result=FAIL_' + type(e).__name__)

# Test the same v2 order family SiRisky uses. No transaction is broadcast.
if wallet:
    try:
        r=requests.get('https://api.jup.ag/swap/v2/order', params={
            'inputMint':'So11111111111111111111111111111111111111112',
            'outputMint':'EPjFWdd5AufqSSqeM2qPZo2mrQSSrSLRYhNACTM1v',
            'amount':'1000000','taker':wallet,'excludeRouters':'jupiterz'
        }, headers=headers, timeout=20)
        print('jupiter_v2_http_status=' + str(r.status_code))
        j=safe_json(r)
        if isinstance(j,dict) and (j.get('transaction') or j.get('outAmount') or j.get('outputAmountResult')):
            print('jupiter_v2_result=PASS')
        else:
            print('jupiter_v2_result=FAIL')
            if isinstance(j,dict):
                msg=j.get('error') or j.get('errorMessage') or j.get('message') or j.get('errorCode') or ''
                print('jupiter_v2_error=' + str(msg)[:300])
    except Exception as e:
        print('jupiter_v2_result=FAIL_' + type(e).__name__)
else:
    print('jupiter_v2_result=SKIP_NO_WALLET_ADDRESS')

print('=== END ===')
PY
