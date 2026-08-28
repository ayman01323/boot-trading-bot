#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323
sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
echo "=== SIRISKY ONE USD SAFETY AUDIT ==="
echo "--- jupiter execution source relevant ---"
grep -n -E "priorit|fee|compute|execute_order|order\(|dynamicCompute|priority" sirisky/jupiter.py 2>/dev/null | head -n 240 || true
echo "--- runtime relevant ---"
grep -Ei "probe|position|reserve|fee|priority|profit|hold|stop|reversal|live_enabled|broadcast_enabled|manual_approval" CSV/runtime.csv 2>/dev/null || true
PYTHONPATH=. .venv/bin/python - <<"PY"
import json, requests
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import wallet_balance_lamports
s=Settings.load(); w=WalletStore(s).address(); rpc=s.resolve_rpc("http")
bal=wallet_balance_lamports(s,w)
print("wallet="+w)
print("wallet_balance_lamports="+str(bal))
print("wallet_balance_sol="+format(bal/1e9,".9f"))
payload={"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":[w,{"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},{"encoding":"jsonParsed","commitment":"confirmed"}]}
r=requests.post(rpc,json=payload,timeout=20); r.raise_for_status(); data=r.json()
if data.get("error"): raise RuntimeError(data["error"])
rows=(data.get("result") or {}).get("value") or []
zero=[]; nonzero=[]
for item in rows:
    acct=item.get("account") or {}; info=(((acct.get("data") or {}).get("parsed") or {}).get("info") or {})
    amt=(((info.get("tokenAmount") or {}).get("amount")) or "0")
    rec={"pubkey":item.get("pubkey"),"mint":info.get("mint"),"lamports":int(acct.get("lamports") or 0),"amount":amt}
    (zero if str(amt)=="0" else nonzero).append(rec)
print("spl_token_accounts="+str(len(rows)))
print("zero_balance_accounts="+str(len(zero)))
print("zero_balance_recoverable_lamports="+str(sum(x["lamports"] for x in zero)))
print("zero_balance_recoverable_sol="+format(sum(x["lamports"] for x in zero)/1e9,".9f"))
print("nonzero_token_accounts="+str(len(nonzero)))
for x in zero[:30]: print("ZERO",json.dumps(x,sort_keys=True))
for x in nonzero[:30]: print("NONZERO",json.dumps(x,sort_keys=True))
print("audit_read_only=true")
PY
'
