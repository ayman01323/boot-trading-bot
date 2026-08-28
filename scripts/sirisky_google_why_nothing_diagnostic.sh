#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== HOST/SERVICE ==="
echo "host=$(hostname)"
echo "service=$(systemctl is-active sirisky.service || true)"
echo "service_since=$(systemctl show sirisky.service -p ActiveEnterTimestamp --value || true)"

echo "=== RUNTIME ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol
s=Settings.load(); rt=s.runtime()
for k in ["trading_mode","live_enabled","broadcast_enabled","manual_approval_enabled","auto_evaluate_candidate_limit","auto_probe_sol","auto_entry_min_sol","auto_entry_max_sol","single_position_only","single_cycle_only","discovery_interval_seconds","jupiter_min_interval_seconds"]:
    print(f"{k}={rt.get(k)}")
print(f"effective_entry_sol={entry_sol(s):.9f}")
PY

echo "=== OPEN POSITIONS ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/open_positions.csv")
rows=list(csv.DictReader(p.open(encoding="utf-8-sig",newline=""))) if p.exists() else []
open_rows=[r for r in rows if str(r.get("status") or "").upper()=="OPEN" and str(r.get("mode") or "").upper()=="LIVE"]
print("open_live_count="+str(len(open_rows)))
for r in open_rows[-5:]: print(r)
PY

echo "=== RECENT CSV ACTIVITY (90 MIN) ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter
cut=datetime.now(timezone.utc)-timedelta(minutes=90)
keys=("timestamp","created_at","updated_at","time","ts")
def dt(v):
    if not v: return None
    s=str(v).strip().replace("Z","+00:00")
    try:
        x=datetime.fromisoformat(s)
        if x.tzinfo is None: x=x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc)
    except Exception: return None
for p in sorted(Path("CSV").glob("*.csv")):
    try:
        with p.open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
    except Exception:
        continue
    recent=[]
    for r in rows[-30000:]:
        t=None
        for k in keys:
            if k in r and r.get(k): t=dt(r.get(k)); break
        if t and t>=cut: recent.append(r)
    if not recent: continue
    reasons=Counter(str(r.get("reason") or r.get("risk_reasons") or r.get("error") or "").strip() for r in recent)
    statuses=Counter(str(r.get("status") or r.get("action") or r.get("decision") or "").strip() for r in recent)
    print(f"FILE={p.name} recent_rows={len(recent)}")
    print("  statuses="+repr(statuses.most_common(12)))
    print("  reasons="+repr([(k,v) for k,v in reasons.most_common(15) if k]))
    keep={k:v for k,v in recent[-1].items() if v and k in {"timestamp","created_at","status","action","decision","reason","risk_reasons","error","mint","pool_id","opportunity_id","strategy_id","forecast_net_pct","temperature"}}
    print("  last="+repr(keep))
PY

echo "=== JUPITER 0.009 SOL FEE GATE NOW (NO BROADCAST) ==="
PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import order, WSOL_MINT, USDC_MINT
from sirisky.safety_v2 import inspect_order_network_fee, _gate_order
s=Settings.load(); wallet=WalletStore(s).address(); amount=9000000
try:
    q=order(s,wallet,WSOL_MINT,USDC_MINT,amount)
    print("fee_estimate="+repr(inspect_order_network_fee(q)))
    try:
        _gate_order(s,q,WSOL_MINT,USDC_MINT,amount)
        print("fee_gate=PASS")
    except Exception as e:
        print("fee_gate=BLOCK:"+str(e))
except Exception as e:
    print("jupiter_order_error="+type(e).__name__+":"+str(e))
PY

echo "=== SERVICE JOURNAL 90 MIN FILTERED ==="
journalctl -u sirisky.service --since "90 minutes ago" --no-pager 2>/dev/null \
  | grep -Ei "discover|candidate|opportun|trigger|stage[0-9]|risk|reject|block|jupiter|quote|fee|open|buy|sell|error|exception|rate|liquidity|forecast|no_trigger|hot|warm|cold" \
  | tail -n 500 || true

echo "=== SERVICE JOURNAL TAIL ==="
journalctl -u sirisky.service -n 120 --no-pager 2>/dev/null || true
'
