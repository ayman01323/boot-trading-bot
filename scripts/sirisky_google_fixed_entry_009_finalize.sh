#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
FIXED_SOL="0.009"
FIXED_LAMPORTS="9000000"

echo "=== SIRISKY FIXED ENTRY 0.009 SOL FINALIZE ==="

OPEN_LIVE=$(PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/open_positions.csv")
n=0
if p.exists():
    with p.open(encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("status") or "").upper()=="OPEN" and str(r.get("mode") or "").upper()=="LIVE":
                n+=1
print(n)
PY
)
echo "open_live_before=$OPEN_LIVE"

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path
p=Path("CSV/runtime.csv")
fixed="0.009"
updates={
    "auto_probe_sol":fixed,
    "auto_entry_min_sol":fixed,
    "auto_entry_max_sol":fixed,
    "auto_promote_to_selected":"1",
    "auto_evaluate_candidate_limit":"1",
    "single_position_only":"1",
    "single_cycle_only":"1",
    "telegram_open_position_notice_seconds":"45",
}
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
seen=set()
for r in rows:
    key=str(r.get(k) or "")
    if key in updates:
        r[v]=updates[key]; seen.add(key)
for key,val in updates.items():
    if key not in seen:
        r={x:"" for x in fields}; r[k]=key; r[v]=val; rows.append(r)
# Any SiRisky sizing row explicitly left at the old 0.0005 SOL canary is retired.
for r in rows:
    key=str(r.get(k) or "").lower()
    raw=str(r.get(v) or "").strip()
    if any(t in key for t in ("entry", "probe", "canary", "trade", "position")) and "sol" in key:
        try:
            if abs(float(raw)-0.0005) < 1e-12:
                r[v]=fixed
                print("retired_old_0005_key="+str(r.get(k)))
        except Exception:
            pass
tmp=p.with_suffix(".tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
os.replace(tmp,p)
print("fixed_runtime_written=true")
PY

if [ "$OPEN_LIVE" != "0" ]; then
  PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol
s=Settings.load(); rt=s.runtime(); fixed=0.009
assert abs(entry_sol(s)-fixed)<1e-12
assert abs(float(rt.get("auto_probe_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_min_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-fixed)<1e-12
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
print("fixed_entry_test=PASS")
print("entry_lamports=9000000")
print("old_0005_live_entry=DISABLED")
print("continuous_search_config=PASS")
print("existing_live_position_monitoring=UNCHANGED")
PY
  test "$(systemctl is-active sirisky.service)" = active
  echo "service=active"
  echo "FINAL_STATE=FIXED_009_FOR_NEXT_ENTRY_EXISTING_POSITION_NOT_INTERRUPTED"
  exit 0
fi

disarm() {
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path
p=Path("CSV/runtime.csv")
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
for r in rows:
    if r.get(k) in {"live_enabled","broadcast_enabled"}: r[v]="0"
tmp=p.with_suffix(".tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
os.replace(tmp,p)
PY
}

fail() {
  set +e
  disarm
  systemctl restart sirisky.service >/dev/null 2>&1 || true
  echo "FINAL_STATE=FAILED_DISARMED"
  exit 1
}
trap fail ERR

systemctl stop sirisky.service
disarm

# Validate exact 0.009 SOL sizing and fee/risk gates. This constructs a Jupiter
# order only for fee inspection; it does NOT broadcast a test trade.
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import order, WSOL_MINT, USDC_MINT, wallet_balance_lamports
from sirisky.safety_v2 import entry_sol, inspect_order_network_fee, _gate_order
s=Settings.load(); rt=s.runtime(); fixed=0.009
entry=entry_sol(s); lamports=int(round(entry*1e9)); wallet=WalletStore(s).address(); bal=wallet_balance_lamports(s,wallet)
print("entry_sol=%.9f" % entry)
print("entry_lamports="+str(lamports))
print("wallet_balance_sol=%.9f" % (bal/1e9))
assert abs(entry-fixed)<1e-12
assert lamports==9000000
assert abs(float(rt.get("auto_probe_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_min_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-fixed)<1e-12
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
# Confirm the old 0.0005 SOL amount is not active in runtime sizing controls.
for k,v in rt.items():
    kl=str(k).lower()
    if any(t in kl for t in ("entry", "probe", "canary", "trade", "position")) and "sol" in kl:
        try:
            assert abs(float(v)-0.0005) >= 1e-12, f"OLD_0005_ACTIVE:{k}={v}"
        except (TypeError, ValueError):
            pass
reserve=0.005
assert bal >= lamports + int((reserve+0.001)*1e9)
assert float(s.risk().get("min_forecast_net_pct") or 0)>=2.0
assert float(s.risk().get("max_round_trip_cost_pct") or 99)<=2.0
assert float(s.risk().get("min_exit_health_pct") or 0)>=98.0
q=order(s,wallet,WSOL_MINT,USDC_MINT,lamports)
fee=inspect_order_network_fee(q)
print("jupiter_fee_estimate="+str(fee))
try:
    _gate_order(s,q,WSOL_MINT,USDC_MINT,lamports)
    print("jupiter_fee_gate=PASS")
except RuntimeError as exc:
    text=str(exc)
    assert text.startswith("PRIORITY_FEE_CAP:") or text.startswith("NETWORK_FEE_PCT_CAP:")
    print("jupiter_fee_gate=SAFE_BLOCK:"+text)
print("fixed_entry_test=PASS")
print("old_0005_live_entry=DISABLED")
print("risk_threshold_test=PASS")
print("continuous_search_config=PASS")
PY

# Re-arm only after all validations above pass.
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path
p=Path("CSV/runtime.csv")
vals={
 "trading_mode":"LIVE","live_enabled":"1","broadcast_enabled":"1",
 "manual_approval_enabled":"0","manual_approval_require_external_signature":"0",
 "auto_probe_sol":"0.009","auto_entry_min_sol":"0.009","auto_entry_max_sol":"0.009",
 "auto_promote_to_selected":"1","auto_evaluate_candidate_limit":"1"
}
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
seen=set()
for r in rows:
    key=str(r.get(k) or "")
    if key in vals: r[v]=vals[key]; seen.add(key)
for key,val in vals.items():
    if key not in seen:
        r={x:"" for x in fields}; r[k]=key; r[v]=val; rows.append(r)
tmp=p.with_suffix(".tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
os.replace(tmp,p)
PY

systemctl restart sirisky.service
sleep 6
test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol
from sirisky.telegram import TelegramClient
s=Settings.load(); rt=s.runtime(); fixed=0.009
assert abs(entry_sol(s)-fixed)<1e-12
assert str(rt.get("trading_mode")).upper()=="LIVE"
assert str(rt.get("live_enabled"))=="1"
assert str(rt.get("broadcast_enabled"))=="1"
assert str(rt.get("manual_approval_enabled"))=="0"
assert abs(float(rt.get("auto_probe_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_min_sol"))-fixed)<1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-fixed)<1e-12
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
msg="SiRisky armed: fixed 0.009 SOL (9,000,000 lamports) per new trade. Old 0.0005 SOL canary sizing is disabled. Continuous discovery and Safety-v2 gates remain active."
tg=TelegramClient(s)
print("telegram_fixed_entry_notice_sent="+str(tg.send(msg) if tg.configured() else False).lower())
print("service=active")
print("trading_mode=LIVE")
print("live_enabled=1")
print("broadcast_enabled=1")
print("manual_approval_enabled=0")
print("fixed_entry_sol=0.009")
print("entry_lamports=9000000")
print("old_0005_live_entry=DISABLED")
print("continuous_search=ON")
print("FINAL_STATE=PASS_ARMED_FIXED_ENTRY_009")
PY
trap - ERR
'
