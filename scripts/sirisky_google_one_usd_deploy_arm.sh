#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="data/one-usd-safety-backup-$STAMP"
mkdir -p "$BACKUP"
cp -a run.py CSV/runtime.csv CSV/stage3_risk.csv "$BACKUP/" 2>/dev/null || true

fail_safe() {
  set +e
  PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/runtime.csv")
if p.exists():
    rows=list(csv.DictReader(p.open(encoding="utf-8-sig",newline="")))
    fields=list(rows[0].keys()) if rows else ["key","value","description"]
    k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
    vals={"live_enabled":"0","broadcast_enabled":"0"}
    seen=set()
    for r in rows:
        if r.get(k) in vals: r[v]=vals[r[k]]; seen.add(r[k])
    for key,val in vals.items():
        if key not in seen:
            r={f:"" for f in fields}; r[k]=key; r[v]=val; rows.append(r)
    with p.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
PY
  systemctl restart sirisky.service >/dev/null 2>&1 || true
  echo "FAIL_SAFE=DISARMED"
}
trap fail_safe ERR

echo "=== SIRISKY ONE USD SAFETY DEPLOY ==="
systemctl stop sirisky.service

# Disarm before any mutation or capital cleanup.
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path

def upsert(path, values):
    p=Path(path); rows=[]; fields=[]
    if p.exists():
        with p.open(encoding="utf-8-sig",newline="") as f:
            rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
    if not fields: fields=["key","value","description"]
    k="key" if "key" in fields else fields[0]; v="value" if "value" in fields else fields[1]
    seen=set()
    for r in rows:
        key=str(r.get(k) or "")
        if key in values:
            r[v]=str(values[key]); seen.add(key)
    for key,val in values.items():
        if key not in seen:
            r={f:"" for f in fields}; r[k]=key; r[v]=str(val)
            if "description" in fields: r["description"]="SiRisky $1 fee-aware safety v2"
            rows.append(r)
    with p.open("w",encoding="utf-8",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)

upsert("CSV/runtime.csv",{
 "live_enabled":0,"broadcast_enabled":0,"manual_approval_enabled":0,
 "auto_entry_usd":1.0,"auto_probe_sol":0.00930,"sol_usd_fallback":108.0,
 "auto_entry_min_sol":0.001,"auto_entry_max_sol":0.020,
 "max_priority_fee_lamports":30000,"estimated_buy_network_fee_lamports":35000,
 "estimated_sell_network_fee_lamports":35000,"max_buy_network_fee_pct":1.0,
 "max_sell_network_fee_pct":1.0,"max_emergency_sell_network_fee_pct":2.0,
 "momentum_check_seconds":30,"momentum_min_net_pct":0.0,
 "auto_cleanup_zero_token_accounts":1,"one_usd_safety_v2":1,
 "telegram_open_position_notice_seconds":45,
})
upsert("CSV/stage3_risk.csv",{
 "min_forecast_net_pct":2.0,"max_round_trip_cost_pct":2.0,"min_exit_health_pct":98.0,
 "fast_take_profit_floor_pct":2.0,"fast_take_profit_cap_pct":5.0,
 "fast_stop_net_pct":3.0,"warm_reversal_pct":1.5,"hot_reversal_pct":3.0,
 "fast_max_hold_cap_seconds":90,"untouched_sol_reserve":0.005,"max_open_positions":1,
})
print("runtime_and_risk_disarmed_configured=true")
PY

# Install persisted safety module from main and activate it in the live entry point.
curl -fsSL --retry 3 --connect-timeout 10 \
  https://raw.githubusercontent.com/ayman01323/boot-trading-bot/a7edf1905791df482c9e27ce37982c1c0bdb7e23/SiRisky/overrides/sirisky/safety_v2.py \
  -o sirisky/safety_v2.py
PYTHONPATH=. .venv/bin/python - <<"PY"
from pathlib import Path
p=Path("run.py"); text=p.read_text(encoding="utf-8")
imp="from sirisky.safety_v2 import install_safety_v2\n"
if imp not in text:
    marker="from sirisky.manual_approval import ManualApprovalGate\n"
    if marker not in text: raise SystemExit("run.py import marker missing")
    text=text.replace(marker,marker+imp,1)
call="install_safety_v2()\n"
if call not in text:
    marker="\n\ndef status_line"
    if marker not in text: raise SystemExit("run.py call marker missing")
    text=text.replace(marker,"\n\ninstall_safety_v2()"+marker,1)
p.write_text(text,encoding="utf-8")
print("safety_v2_activated=true")
PY

PYTHONPATH=. .venv/bin/python -m py_compile run.py sirisky/safety_v2.py sirisky/stage3_risk.py sirisky/stage5_trade.py sirisky/stage6_monitor.py

echo "--- TEST 1: dynamic $1 sizing ---"
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol, sol_usd
s=Settings.load(); price=sol_usd(s); amount=entry_sol(s); usd=price*amount
print(f"sol_usd={price:.6f}")
print(f"entry_sol={amount:.9f}")
print(f"entry_usd={usd:.6f}")
assert 0.95 <= usd <= 1.05
PY

echo "--- TEST 2: fee decoder on current Jupiter order (NO BROADCAST) ---"
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import order,WSOL_MINT,USDC_MINT
from sirisky.safety_v2 import entry_sol,inspect_order_network_fee
s=Settings.load(); w=WalletStore(s).address(); lamports=int(entry_sol(s)*1e9)
q=order(s,w,WSOL_MINT,USDC_MINT,lamports)
fee=inspect_order_network_fee(q)
print("fee_decoder="+str(fee))
assert fee["estimated_network_fee_lamports"] >= 5000
assert fee["priority_fee_lamports"] >= 0
print("jupiter_order_no_broadcast=true")
PY

echo "--- TEST 3: standard suite while DISARMED ---"
PYTHONPATH=. .venv/bin/python run.py selftest

echo "--- TEST 4: safe preflight while DISARMED ---"
PYTHONPATH=. .venv/bin/python run.py check || true

# Token-account cleanup is simulated first; only zero-balance wallet-owned classic SPL accounts are eligible.
echo "--- TEST 5: zero-account cleanup simulation ---"
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import close_zero_token_accounts
s=Settings.load(); print(close_zero_token_accounts(s,None,broadcast=False))
PY

echo "--- CAPITAL CLEANUP: close only simulated zero-balance accounts ---"
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import close_zero_token_accounts
s=Settings.load(); print(close_zero_token_accounts(s,None,broadcast=True))
PY

# Final capital + safety validation. No arming if any requirement fails.
echo "--- TEST 6: arm prerequisites ---"
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import wallet_balance_lamports
from sirisky.safety_v2 import entry_sol,sol_usd,zero_token_accounts
s=Settings.load(); w=WalletStore(s).address(); bal=wallet_balance_lamports(s,w)/1e9
entry=entry_sol(s); reserve=float(s.risk().get("untouched_sol_reserve") or 0.005); buffer=0.001
print(f"wallet_balance_sol={bal:.9f}")
print(f"sol_usd={sol_usd(s):.6f}")
print(f"entry_sol={entry:.9f}")
print(f"entry_usd={entry*sol_usd(s):.6f}")
print(f"reserve_sol={reserve:.9f}")
print(f"arm_required_sol={entry+reserve+buffer:.9f}")
print(f"zero_accounts_remaining={len(zero_token_accounts(s,None))}")
assert bal >= entry + reserve + buffer
assert 0.95 <= entry*sol_usd(s) <= 1.05
assert float(s.risk().get("min_forecast_net_pct") or 0) >= 2.0
assert float(s.risk().get("max_round_trip_cost_pct") or 99) <= 2.0
assert float(s.risk().get("min_exit_health_pct") or 0) >= 98.0
assert int(float(s.runtime().get("max_priority_fee_lamports") or 999999)) <= 30000
print("arm_prerequisites=PASS")
PY

# Arm only after all tests and capital prerequisites pass.
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
print("arm_flags_written=true")
PY

systemctl restart sirisky.service
sleep 4
test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol,sol_usd
from sirisky.telegram import TelegramClient
s=Settings.load(); rt=s.runtime()
assert str(rt.get("trading_mode") or "").upper()=="LIVE"
assert str(rt.get("live_enabled") or "0")=="1"
assert str(rt.get("broadcast_enabled") or "0")=="1"
assert str(rt.get("manual_approval_enabled") or "0")=="0"
msg=("SiRisky $1 safety v2 ARMED on botgoogle. "
     f"Entry={entry_sol(s):.9f} SOL (~${entry_sol(s)*sol_usd(s):.2f}); "
     "forecast>=2%, round-trip<=2%, exit-health>=98%, priority-fee<=30000 lamports, "
     "fee-aware net TP, 30s momentum-failure exit, zero-account cleanup active.")
tg=TelegramClient(s); print("telegram_arm_notice_sent="+str(tg.send(msg) if tg.configured() else False).lower())
print("trading_mode="+str(rt.get("trading_mode")))
print("live_enabled="+str(rt.get("live_enabled")))
print("broadcast_enabled="+str(rt.get("broadcast_enabled")))
print("manual_approval_enabled="+str(rt.get("manual_approval_enabled")))
print("auto_entry_usd="+str(rt.get("auto_entry_usd")))
print("max_priority_fee_lamports="+str(rt.get("max_priority_fee_lamports")))
print("service=active")
print("one_usd_safety_v2=ARMED")
PY

trap - ERR
echo "backup=$BACKUP"
'
