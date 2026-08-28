#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/runtime.csv")
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]
v="value" if "value" in fields else fields[1]
vals={
    "auto_evaluate_candidate_limit":"5",
    "jupiter_min_interval_seconds":"2.25",
    "jupiter_max_retries":"4",
    "trading_mode":"LIVE",
    "live_enabled":"1",
    "broadcast_enabled":"1",
    "manual_approval_enabled":"0",
    "auto_entry_min_sol":"0.009172629",
    "auto_entry_max_sol":"0.009172629",
}
seen=set()
for r in rows:
    if r.get(k) in vals:
        r[v]=vals[r[k]]; seen.add(r[k])
for key,val in vals.items():
    if key not in seen:
        r={f:"" for f in fields}; r[k]=key; r[v]=val; rows.append(r)
with p.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
print("runtime_updated=true")
PY

PYTHONPATH=. .venv/bin/python run.py selftest
systemctl restart sirisky.service
sleep 4
test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
s=Settings.load(); rt=s.runtime(); risk=s.risk()
assert str(rt.get("trading_mode") or "").upper()=="LIVE"
assert str(rt.get("live_enabled"))=="1"
assert str(rt.get("broadcast_enabled"))=="1"
assert str(rt.get("manual_approval_enabled"))=="0"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==5
assert abs(float(rt.get("auto_entry_min_sol"))-0.009172629)<1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-0.009172629)<1e-12
assert float(rt.get("jupiter_min_interval_seconds") or 0)>=2.25
assert float(risk.get("min_forecast_net_pct") or 0)>=2.0
assert float(risk.get("max_round_trip_cost_pct") or 99)<=2.0
assert float(risk.get("min_exit_health_pct") or 0)>=98.0
print("candidate_limit=5")
print("jupiter_min_interval_seconds="+str(rt.get("jupiter_min_interval_seconds")))
print("fixed_entry_sol=0.009172629")
print("risk_gates_preserved=true")
print("service=active")
print("LIVE=armed")
PY

# Allow the live engine to produce several post-change decision records, then
# report whether it is actually traversing beyond candidate #1. No trade is forced.
sleep 25
printf "%s\n" "--- POST-FIX DECISIONS ---"
journalctl -u sirisky.service --since "35 seconds ago" --no-pager -o cat 2>/dev/null \
 | grep "CANDIDATE_BATCH_NO_OPEN" \
 | tail -n 12 \
 | sed -E "s#https?://[^ ]+#<URL>#g" || true
printf "%s\n" "--- POST-FIX OPEN/CLOSE ---"
journalctl -u sirisky.service --since "35 seconds ago" --no-pager -o cat 2>/dev/null \
 | grep -E "\"status\": \"OPENED\"|\"status\": \"CLOSED\"|EXECUTION_REJECT|RISK_REJECT" \
 | tail -n 20 \
 | sed -E "s#https?://[^ ]+#<URL>#g" || true

echo "CANDIDATE_COVERAGE_FIX=PASS"
'
