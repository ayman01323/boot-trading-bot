#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY LIVE TRADE DIAGNOSTIC ==="
echo "host=$(hostname)"
echo "service=$(systemctl is-active sirisky.service || true)"

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, json, time
from pathlib import Path
from sirisky.config import Settings
from sirisky.wallet import WalletStore
from sirisky.jupiter import wallet_balance_lamports

s=Settings.load(); rt=s.runtime(); risk=s.risk()
keys=[
 "trading_mode","live_enabled","broadcast_enabled","manual_approval_enabled",
 "auto_entry_min_sol","auto_entry_max_sol","auto_evaluate_candidate_limit",
 "poll_seconds","discovery_interval_seconds","max_priority_fee_lamports",
 "max_buy_network_fee_pct","max_sell_network_fee_pct","max_emergency_sell_network_fee_pct",
]
for k in keys:
    print(f"runtime.{k}={rt.get(k)}")
for k in ["min_forecast_net_pct","max_round_trip_cost_pct","min_exit_health_pct","max_open_positions","untouched_reserve_sol","fast_take_profit_floor_pct","fast_take_profit_cap_pct","fast_stop_net_pct"]:
    print(f"risk.{k}={risk.get(k)}")
try:
    w=WalletStore(s).address(); bal=wallet_balance_lamports(s,w)
    print(f"wallet={w}")
    print(f"wallet_balance_sol={bal/1e9:.9f}")
except Exception as exc:
    print(f"wallet_read_error={type(exc).__name__}")

# Open positions summary only; no keys/secrets.
for fn in ["open_positions.csv","opportunities.csv","stage3_results.csv","executions.csv"]:
    p=s.csv_dir/fn
    if not p.exists():
        print(f"csv.{fn}=MISSING")
        continue
    try:
        with p.open(encoding="utf-8-sig",newline="") as f:
            rows=list(csv.DictReader(f))
        print(f"csv.{fn}.rows={len(rows)}")
        if fn=="open_positions.csv":
            live=[r for r in rows if str(r.get("mode") or "").upper()=="LIVE" and str(r.get("status") or "OPEN").upper()=="OPEN"]
            print(f"open_live_positions={len(live)}")
            for r in live[-3:]:
                print("open="+json.dumps({k:r.get(k) for k in ["position_id","mint","pool_id","entry_sol","entry_lamports","opened_epoch","status"]},separators=(",",":")))
        else:
            for r in rows[-8:]:
                keep={}
                for k in ["timestamp","epoch","created_epoch","updated_epoch","mint","pool_id","status","decision","reason","reasons","forecast_net_pct","exit_health_pct","round_trip_cost_pct","action","success","error","mode","position_id"]:
                    if k in r and str(r.get(k) or "")!="": keep[k]=r.get(k)
                if keep: print(f"recent.{fn}="+json.dumps(keep,separators=(",",":")))
    except Exception as exc:
        print(f"csv.{fn}.error={type(exc).__name__}")
PY

echo "--- RECENT SERVICE DECISIONS (last 20 min) ---"
journalctl -u sirisky.service --since "20 minutes ago" --no-pager -o cat 2>/dev/null \
 | grep -E "CANDIDATE_BATCH_NO_OPEN|RISK_REJECT|EXECUTION_REJECT|OPENED|CLOSED|HOLD|INSUFFICIENT|FORECAST|ROUND_TRIP|EXIT_HEALTH|HOT_NO_ENTRY|NO_EXECUTABLE|PRIORITY_FEE|NETWORK_FEE|RATE_LIMIT|Jupiter|cycle error|Stage 3|stage3|Reason|reasons" \
 | tail -n 180 \
 | sed -E "s#https?://[^ ]+#<URL>#g" || true

echo "--- SERVICE TAIL ---"
journalctl -u sirisky.service -n 80 --no-pager -o cat 2>/dev/null \
 | sed -E "s#https?://[^ ]+#<URL>#g" || true

echo "=== END DIAGNOSTIC ==="
'
