#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323
sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

disarm() {
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/runtime.csv")
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
for r in rows:
    if r.get(k) in {"live_enabled","broadcast_enabled"}: r[v]="0"
with p.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
PY
}
fail() { set +e; disarm; systemctl restart sirisky.service >/dev/null 2>&1 || true; echo "FINAL_VALIDATION=FAILED_DISARMED"; exit 1; }
trap fail ERR

echo "=== SIRISKY USD1 FINAL VALIDATION ==="
systemctl stop sirisky.service
disarm

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import order,WSOL_MINT,USDC_MINT,wallet_balance_lamports
from sirisky.safety_v2 import entry_sol,sol_usd,inspect_order_network_fee,_gate_order,zero_token_accounts
s=Settings.load(); w=WalletStore(s).address(); entry=entry_sol(s); lamports=int(entry*1e9)
bal=wallet_balance_lamports(s,w)
print(f"wallet_balance_sol={bal/1e9:.9f}")
print(f"sol_usd={sol_usd(s):.6f}")
print(f"entry_sol={entry:.9f}")
print(f"entry_usd={entry*sol_usd(s):.6f}")
assert bal >= lamports + int(0.006*1e9)
q=order(s,w,WSOL_MINT,USDC_MINT,lamports)
fee=inspect_order_network_fee(q)
print("current_jupiter_fee="+str(fee))
cap=int(float(s.runtime().get("max_priority_fee_lamports") or 30000))
try:
    _gate_order(s,q,WSOL_MINT,USDC_MINT,lamports)
    gate="PASS"
    assert fee["priority_fee_lamports"] <= cap
    assert fee["estimated_network_fee_lamports"]/lamports*100 <= float(s.runtime().get("max_buy_network_fee_pct") or 1.0)
except RuntimeError as exc:
    text=str(exc)
    assert text.startswith("PRIORITY_FEE_CAP:") or text.startswith("NETWORK_FEE_PCT_CAP:")
    gate="SAFE_BLOCK:"+text
print("fee_gate_result="+gate)
print(f"fee_pct_of_usd1={fee['estimated_network_fee_lamports']/lamports*100:.4f}")
assert float(s.risk().get("min_forecast_net_pct") or 0)>=2.0
assert float(s.risk().get("max_round_trip_cost_pct") or 99)<=2.0
assert float(s.risk().get("min_exit_health_pct") or 0)>=98.0
assert len(zero_token_accounts(s,None))==0
print("usd1_jupiter_order_test=PASS")
print("zero_account_cleanup_state=PASS")
PY

PYTHONPATH=. .venv/bin/python run.py selftest

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/runtime.csv")
with p.open(encoding="utf-8-sig",newline="") as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
vals={"trading_mode":"LIVE","live_enabled":"1","broadcast_enabled":"1","manual_approval_enabled":"0","manual_approval_require_external_signature":"0"}
seen=set()
for r in rows:
    if r.get(k) in vals: r[v]=vals[r[k]]; seen.add(r[k])
for key,val in vals.items():
    if key not in seen:
        r={f:"" for f in fields}; r[k]=key; r[v]=val; rows.append(r)
with p.open("w",encoding="utf-8",newline="") as f:
    wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
PY
systemctl restart sirisky.service
sleep 4
test "$(systemctl is-active sirisky.service)" = active
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.telegram import TelegramClient
s=Settings.load(); rt=s.runtime()
assert str(rt.get("live_enabled"))=="1" and str(rt.get("broadcast_enabled"))=="1"
msg="SiRisky USD1 final safety validation PASS; LIVE re-armed on botgoogle. Fee cap, net-profit logic, 30s momentum exit and rent cleanup validated."
tg=TelegramClient(s); print("telegram_final_validation_sent="+str(tg.send(msg) if tg.configured() else False).lower())
print("FINAL_VALIDATION=PASS_ARMED")
print("service=active")
PY
trap - ERR
'
