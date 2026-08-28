#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
FIXED_SOL="0.009172629"

echo "=== SIRISKY FIXED ENTRY 0.009172629 SOL FINALIZE ==="

# Never interrupt monitoring of an existing LIVE position merely to change the
# size used for the next entry. The runtime CSV update below is atomic.
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
fixed="0.009172629"
updates={
    "auto_probe_sol":fixed,
    "auto_entry_min_sol":fixed,
    "auto_entry_max_sol":fixed,
    "auto_entry_usd":"1.0",
    "sol_usd_fallback":"109.02",
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
tmp=p.with_suffix(".tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
os.replace(tmp,p)
print("fixed_runtime_written=true")
PY

# If a LIVE position is already open, do not stop/restart the service. The fixed
# size applies to the next entry and continuous exit monitoring stays untouched.
if [ "$OPEN_LIVE" != "0" ]; then
  PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol
s=Settings.load(); rt=s.runtime(); fixed=0.009172629
assert abs(entry_sol(s)-fixed)<1e-12
assert str(rt.get("auto_probe_sol"))=="0.009172629"
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
print("fixed_entry_test=PASS")
print("continuous_search_config=PASS")
print("existing_live_position_monitoring=UNCHANGED")
PY
  test "$(systemctl is-active sirisky.service)" = active
  echo "service=active"
  echo "FINAL_STATE=FIXED_FOR_NEXT_ENTRY_EXISTING_POSITION_NOT_INTERRUPTED"
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

# Reclaim only classic SPL token accounts that are wallet-owned and still have
# exactly zero token balance. Non-zero accounts are never included by safety_v2.
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import zero_token_accounts, close_zero_token_accounts
s=Settings.load()
rows=zero_token_accounts(s,None)
print("zero_accounts_before="+str(len(rows)))
print("recoverable_sol_before=%.9f" % (sum(int(r.get("lamports") or 0) for r in rows)/1e9))
if rows:
    sim=close_zero_token_accounts(s,None,broadcast=False)
    assert sim.get("status")=="SIMULATED"
    print("zero_account_close_simulation=PASS")
    result=close_zero_token_accounts(s,None,broadcast=True)
    print("zero_account_cleanup_status="+str(result.get("status")))
    print("zero_account_cleanup_count="+str(result.get("count")))
    print("zero_account_cleanup_signature="+str(result.get("signature") or ""))
remaining=zero_token_accounts(s,None)
print("zero_accounts_after="+str(len(remaining)))
assert len(remaining)==0
PY

# Fixed-size and safety validation. Jupiter order construction is quote/order-only;
# no trade is broadcast by this test. A fee-cap rejection is an acceptable PASS.
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import order, WSOL_MINT, USDC_MINT, wallet_balance_lamports
from sirisky.safety_v2 import entry_sol, inspect_order_network_fee, _gate_order
s=Settings.load(); rt=s.runtime(); fixed=0.009172629
entry=entry_sol(s); lamports=int(round(entry*1e9)); wallet=WalletStore(s).address(); bal=wallet_balance_lamports(s,wallet)
print("entry_sol=%.9f" % entry)
print("entry_lamports="+str(lamports))
print("wallet_balance_sol=%.9f" % (bal/1e9))
assert abs(entry-fixed)<1e-12
assert lamports==9172629
assert str(rt.get("auto_probe_sol"))=="0.009172629"
assert str(rt.get("auto_entry_min_sol"))=="0.009172629"
assert str(rt.get("auto_entry_max_sol"))=="0.009172629"
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
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
print("risk_threshold_test=PASS")
print("continuous_search_config=PASS")
PY

PYTHONPATH=. .venv/bin/python run.py selftest

# Re-arm only after all checks above pass.
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path
p=Path("CSV/runtime.csv")
vals={
 "trading_mode":"LIVE","live_enabled":"1","broadcast_enabled":"1",
 "manual_approval_enabled":"0","manual_approval_require_external_signature":"0",
 "auto_probe_sol":"0.009172629","auto_entry_min_sol":"0.009172629","auto_entry_max_sol":"0.009172629",
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
s=Settings.load(); rt=s.runtime(); fixed=0.009172629
assert abs(entry_sol(s)-fixed)<1e-12
assert str(rt.get("trading_mode")).upper()=="LIVE"
assert str(rt.get("live_enabled"))=="1"
assert str(rt.get("broadcast_enabled"))=="1"
assert str(rt.get("manual_approval_enabled"))=="0"
assert str(rt.get("auto_promote_to_selected"))=="1"
assert int(float(rt.get("auto_evaluate_candidate_limit") or 0))==1
msg="SiRisky fixed entry armed: 0.009172629 SOL per new trade. Continuous discovery remains ON. Safety-v2 fee/net-profit/momentum/risk gates remain active."
tg=TelegramClient(s)
print("telegram_fixed_entry_notice_sent="+str(tg.send(msg) if tg.configured() else False).lower())
print("service=active")
print("trading_mode=LIVE")
print("live_enabled=1")
print("broadcast_enabled=1")
print("manual_approval_enabled=0")
print("fixed_entry_sol=0.009172629")
print("continuous_search=ON")
print("FINAL_STATE=PASS_ARMED_FIXED_ENTRY")
PY
trap - ERR
'
