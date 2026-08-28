#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323
TARGET='6LspdeZhf6HX7YdMuaz3gVUdXW9ifs15cyRTdo3aS3Xr'

sudo -n env TARGET="$TARGET" bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
echo "=== WALLET ATTRIBUTION ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import os
from sirisky.config import Settings
from sirisky.wallet import WalletStore
s=Settings.load(); target=os.environ["TARGET"]; wallet=WalletStore(s).address()
print("target="+target)
print("sirisky_wallet="+wallet)
print("sirisky_wallet_match="+str(wallet==target).lower())
PY

echo "=== TARGET REFERENCES ==="
grep -R -n -F "$TARGET" CSV 2>/dev/null | tail -n 80 || true

echo "=== LIVE EXECUTIONS ==="
head -n 1 CSV/executions.csv 2>/dev/null || true
awk -F, '\''NR==1 || $5=="LIVE"'\'' CSV/executions.csv 2>/dev/null | tail -n 40 || true

echo "=== RECENT STRATEGY LINKS ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
D=Path("CSV"); ep=D/"executions.csv"
if not ep.exists(): raise SystemExit
rows=list(csv.DictReader(ep.open(encoding="utf-8-sig",newline="")))
recent=[r for r in rows if (r.get("mode") or "").upper()=="LIVE"][-20:]
mints={r.get("mint") for r in recent if r.get("mint")}
print("live_execution_count="+str(len([r for r in rows if (r.get("mode") or "").upper()=="LIVE"])))
for r in recent:
    print("EXEC", {k:r.get(k) for k in r if r.get(k) and k in {"timestamp","order_id","action","mint","mode","status","signature","amount_in_raw","amount_out_raw","reason","error"}})
for p in sorted(D.glob("*.csv")):
    if p.name=="executions.csv": continue
    try: rr=list(csv.DictReader(p.open(encoding="utf-8-sig",newline="")))
    except Exception: continue
    hits=[]
    for r in rr:
        txt="|".join(str(v) for v in r.values())
        if any(m and m in txt for m in mints): hits.append(r)
    if hits:
        print("FILE="+p.name)
        for r in hits[-12:]:
            keep={k:v for k,v in r.items() if v and k in {"timestamp","opportunity_id","pool_id","mint","strategy_id","action","status","reason","mode","position_id","order_id","source","leader_wallet","wallet","temperature","risk_reasons","forecast_net_pct"}}
            if keep: print(keep)
PY

echo "service=$(systemctl is-active sirisky.service)"
echo "trace_read_only=true"
'
