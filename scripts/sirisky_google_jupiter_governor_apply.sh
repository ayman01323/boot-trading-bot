#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY ON-CHAIN CAPITAL STUDY ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, json, requests
from pathlib import Path
from sirisky.config import Settings
from sirisky.wallet import WalletStore

D=Path("CSV")
s=Settings.load()
wallet=WalletStore(s).address()
rpc=s.resolve_rpc("http")
rows=list(csv.DictReader((D/"executions.csv").open(encoding="utf-8-sig",newline="")))
succ=[r for r in rows if (r.get("mode") or "").upper()=="LIVE" and (r.get("status") or "").upper()=="SUCCESS" and r.get("signature")]
succ.sort(key=lambda r:int(float(r.get("timestamp") or 0)))
if not succ:
    raise SystemExit("NO_SUCCESSFUL_LIVE_EXECUTIONS")

def tx(sig):
    payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","commitment":"confirmed","maxSupportedTransactionVersion":0}]}
    rr=requests.post(rpc,json=payload,timeout=20); rr.raise_for_status(); data=rr.json()
    if data.get("error"): raise RuntimeError(data["error"])
    return data.get("result") or {}

def wallet_balances(result):
    msg=((result.get("transaction") or {}).get("message") or {})
    keys=msg.get("accountKeys") or []
    pubs=[]
    for k in keys:
        pubs.append(k.get("pubkey") if isinstance(k,dict) else str(k))
    if wallet not in pubs:
        return None,None,None
    i=pubs.index(wallet); meta=result.get("meta") or {}
    pre=(meta.get("preBalances") or [])[i]
    post=(meta.get("postBalances") or [])[i]
    return int(pre),int(post),int(meta.get("fee") or 0)

print("wallet="+wallet)
print("successful_live_transactions="+str(len(succ)))
first=succ[0]; last=succ[-1]
first_tx=tx(first["signature"]); last_tx=tx(last["signature"])
pre0,post0,fee0=wallet_balances(first_tx)
preN,postN,feeN=wallet_balances(last_tx)
print("first_timestamp="+str(first.get("timestamp") or ""))
print("first_action="+str(first.get("action") or ""))
print("first_signature="+first["signature"])
print("wallet_start_lamports="+str(pre0))
print("wallet_start_sol="+format(pre0/1e9,".9f"))
print("last_timestamp="+str(last.get("timestamp") or ""))
print("last_action="+str(last.get("action") or ""))
print("last_signature="+last["signature"])
print("wallet_end_lamports="+str(postN))
print("wallet_end_sol="+format(postN/1e9,".9f"))
print("wallet_delta_lamports="+str(postN-pre0))
print("wallet_delta_sol="+format((postN-pre0)/1e9,".9f"))

fees=0
for r in succ:
    result=tx(r["signature"]); pre,post,fee=wallet_balances(result); fees+=fee or 0
    print("TX",json.dumps({
        "timestamp":r.get("timestamp"),"action":r.get("action"),"mint":r.get("mint"),
        "signature":r.get("signature"),"wallet_pre_sol":None if pre is None else round(pre/1e9,9),
        "wallet_post_sol":None if post is None else round(post/1e9,9),"network_fee_sol":round((fee or 0)/1e9,9),
        "input_raw":r.get("input_raw"),"output_raw":r.get("output_raw"),"reason":r.get("reason")
    },sort_keys=True))
print("successful_tx_network_fees_lamports="+str(fees))
print("successful_tx_network_fees_sol="+format(fees/1e9,".9f"))
print("study_read_only=true")
PY
'
