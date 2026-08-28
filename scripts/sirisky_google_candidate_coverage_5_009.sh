#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY 0.009 SOL CANDIDATE COVERAGE 5 ==="

OPEN_LIVE=$(PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/open_positions.csv")
n=0
if p.exists():
    with p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("status") or "").upper()=="OPEN" and str(r.get("mode") or "").upper()=="LIVE":
                n += 1
print(n)
PY
)
echo "open_live_before=$OPEN_LIVE"

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path
p=Path("CSV/runtime.csv")
with p.open(encoding="utf-8-sig", newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]
v="value" if "value" in fields else fields[1]
updates={
    "auto_evaluate_candidate_limit":"5",
    "auto_probe_sol":"0.009",
    "auto_entry_min_sol":"0.009",
    "auto_entry_max_sol":"0.009",
}
seen=set()
for r in rows:
    key=str(r.get(k) or "")
    if key in updates:
        r[v]=updates[key]; seen.add(key)
for key,val in updates.items():
    if key not in seen:
        r={x:"" for x in fields}; r[k]=key; r[v]=val; rows.append(r)
tmp=p.with_suffix(".tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
os.replace(tmp,p)
print("runtime_updated=true")
PY

if [ "$OPEN_LIVE" = "0" ]; then
  systemctl restart sirisky.service
  sleep 5
fi

test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol
s=Settings.load(); rt=s.runtime()
assert abs(entry_sol(s)-0.009) < 1e-12
assert abs(float(rt.get("auto_probe_sol"))-0.009) < 1e-12
assert abs(float(rt.get("auto_entry_min_sol"))-0.009) < 1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-0.009) < 1e-12
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0)) == 5
assert str(rt.get("trading_mode")).upper() == "LIVE"
assert str(rt.get("live_enabled")) == "1"
assert str(rt.get("broadcast_enabled")) == "1"
assert str(rt.get("manual_approval_enabled")) == "0"
print("service=active")
print("trading_mode=LIVE")
print("live_enabled=1")
print("broadcast_enabled=1")
print("manual_approval_enabled=0")
print("fixed_entry_sol=0.009")
print("entry_lamports=9000000")
print("candidate_limit=5")
print("old_0005_live_entry=DISABLED")
print("FINAL_STATE=PASS_LIVE_009_CANDIDATES_5")
PY
'
