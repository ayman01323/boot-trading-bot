#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY COMPACT BLOCKER REPORT ==="
echo "host=$(hostname)"
echo "service=$(systemctl is-active sirisky.service || true)"
echo "service_since=$(systemctl show sirisky.service -p ActiveEnterTimestamp --value || true)"

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol

s=Settings.load(); rt=s.runtime()
for k in ["trading_mode","live_enabled","broadcast_enabled","manual_approval_enabled","auto_evaluate_candidate_limit","auto_probe_sol","auto_entry_min_sol","auto_entry_max_sol","single_position_only","single_cycle_only","discovery_interval_seconds","jupiter_min_interval_seconds"]:
    print(f"runtime_{k}={rt.get(k)}")
print(f"effective_entry_sol={entry_sol(s):.9f}")

p=Path("CSV/open_positions.csv")
rows=list(csv.DictReader(p.open(encoding="utf-8-sig",newline=""))) if p.exists() else []
open_rows=[r for r in rows if str(r.get("status") or "").upper()=="OPEN" and str(r.get("mode") or "").upper()=="LIVE"]
print("open_live_count="+str(len(open_rows)))

cut=datetime.now(timezone.utc)-timedelta(minutes=90)
keys=("timestamp","created_at","updated_at","time","ts")
def parse_dt(v):
    if not v: return None
    s=str(v).strip().replace("Z","+00:00")
    try:
        d=datetime.fromisoformat(s)
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

all_status=Counter(); all_reason=Counter(); file_counts=[]; newest=None; newest_file=""
for fp in sorted(Path("CSV").glob("*.csv")):
    try:
        with fp.open(encoding="utf-8-sig",newline="") as f:
            data=list(csv.DictReader(f))
    except Exception:
        continue
    n=0
    for r in data[-30000:]:
        t=None
        for k in keys:
            if r.get(k): t=parse_dt(r.get(k)); break
        if not t or t<cut: continue
        n+=1
        if newest is None or t>newest:
            newest=t; newest_file=fp.name
        st=str(r.get("status") or r.get("action") or r.get("decision") or "").strip()
        rs=str(r.get("reason") or r.get("risk_reasons") or r.get("error") or "").strip()
        if st: all_status[st]+=1
        if rs: all_reason[rs]+=1
    if n: file_counts.append((fp.name,n))
print("recent_90m_rows="+str(sum(n for _,n in file_counts)))
print("recent_90m_files="+repr(sorted(file_counts,key=lambda x:x[1],reverse=True)[:12]))
print("top_statuses="+repr(all_status.most_common(12)))
print("top_reasons="+repr(all_reason.most_common(15)))
print("newest_activity_utc="+(newest.isoformat() if newest else "NONE"))
print("newest_activity_file="+newest_file)
PY

echo "=== CURRENT JUPITER FEE GATE (NO BROADCAST) ==="
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
    except Exception as exc:
        print("fee_gate=BLOCK:"+str(exc))
except Exception as exc:
    print("jupiter_order_error="+type(exc).__name__+":"+str(exc))
PY

J="$(mktemp)"; trap '"'"'rm -f "$J"'"'"' EXIT
journalctl -u sirisky.service --since "90 minutes ago" --no-pager > "$J" 2>/dev/null || true
echo "journal_lines_90m=$(wc -l < "$J" | tr -d " ")"
for pattern in "discover" "candidate" "NO_TRIGGER" "reject" "block" "jupiter" "quote" "fee" "BUY" "OPENED" "error" "exception"; do
  count=$(grep -Eic "$pattern" "$J" 2>/dev/null || true)
  echo "journal_${pattern//[^A-Za-z0-9]/_}_count=$count"
done
echo "=== RECENT FILTERED JOURNAL ==="
grep -Ei "discover|candidate|opportun|trigger|stage[0-9]|risk|reject|block|jupiter|quote|fee|open|buy|sell|error|exception|rate|liquidity|forecast|no_trigger|hot|warm|cold" "$J" | tail -n 35 || true

echo "=== END COMPACT REPORT ==="
'
