#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY LIVE TRADE CAPITAL / LP STUDY ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, json
from pathlib import Path
D=Path("CSV")

# Pull only successful LIVE BUYs; these are the entries whose selection conditions matter.
ep=D/"executions.csv"
rows=list(csv.DictReader(ep.open(encoding="utf-8-sig",newline=""))) if ep.exists() else []
buys=[r for r in rows if (r.get("mode") or "").upper()=="LIVE" and (r.get("action") or "").upper()=="BUY" and (r.get("status") or "").upper()=="SUCCESS"]
print("successful_live_buys="+str(len(buys)))
for b in buys:
    print("BUY",json.dumps(b,sort_keys=True))

mints={r.get("mint") for r in buys if r.get("mint")}
interesting_terms=(
    "liquid","reserve","depth","fdv","market","cap","volume","price","impact","slippage",
    "round_trip","exit_health","tvl","pool","quote","age","forecast","strategy","temperature",
    "holder","lp_","rug","supply","created","pair"
)

for p in sorted(D.glob("*.csv")):
    if p.name=="executions.csv":
        continue
    try:
        rr=list(csv.DictReader(p.open(encoding="utf-8-sig",newline="")))
    except Exception:
        continue
    hits=[]
    for r in rr:
        mint=str(r.get("mint") or "")
        if mint in mints:
            compact={}
            for k,v in r.items():
                if v in (None,""):
                    continue
                lk=k.lower()
                if k in {"timestamp","opportunity_id","pool_id","mint","strategy_id","position_id","order_id","mode","status","action","reason","risk_reasons"} or any(t in lk for t in interesting_terms):
                    compact[k]=v
            if compact:
                hits.append(compact)
    if hits:
        print("FILE="+p.name)
        print("HEADERS="+",".join(rr[0].keys()) if rr else "HEADERS=")
        for h in hits[-30:]:
            print(json.dumps(h,sort_keys=True))

print("service=active")
PY

echo "study_read_only=true"
'
