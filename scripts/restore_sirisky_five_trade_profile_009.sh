#!/usr/bin/env bash
set -Eeuo pipefail

root=/root/SiRisky
csv="$root/CSV"
snap="$root/data/one-usd-safety-backup-20260828T152708Z"
service=sirisky.service

[[ $(id -u) -eq 0 ]] || { echo 'REFUSE: run as root'; exit 40; }
[[ -f "$snap/runtime.csv" ]] || { echo 'REFUSE: preserved runtime snapshot missing'; exit 41; }
[[ -f "$snap/stage3_risk.csv" ]] || { echo 'REFUSE: preserved Stage-3 risk snapshot missing'; exit 42; }
[[ -f "$csv/runtime.csv" && -f "$csv/stage3_risk.csv" ]] || { echo 'REFUSE: current SiRisky CSV files missing'; exit 43; }

# Freeze SiRisky before checking exposure so a new entry cannot race this restore.
was_active=0
if systemctl is-active --quiet "$service"; then was_active=1; fi
systemctl stop "$service"

open_rows=0
if [[ -f "$csv/open_positions.csv" ]]; then
  open_rows=$(tail -n +2 "$csv/open_positions.csv" | sed '/^[[:space:]]*$/d' | wc -l)
fi
echo "pre_restore_open_positions=$open_rows"
if [[ "$open_rows" -ne 0 ]]; then
  (( was_active == 0 )) || systemctl start "$service"
  echo 'REFUSE: SiRisky has an open position; settings not changed'
  exit 44
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$root/data/pre-five-trade-009-restore-$stamp"
mkdir -p "$backup"
cp -a "$csv/runtime.csv" "$backup/runtime.csv"
cp -a "$csv/stage3_risk.csv" "$backup/stage3_risk.csv"
[[ ! -f "$csv/risk.csv" ]] || cp -a "$csv/risk.csv" "$backup/risk.csv"
echo "pre_restore_backup=$backup"

changed=0
rollback() {
  rc=$?
  if [[ "$rc" -ne 0 && "$changed" -eq 1 ]]; then
    echo 'RESTORE_FAILED: rolling SiRisky settings back'
    cp -a "$backup/runtime.csv" "$csv/runtime.csv"
    cp -a "$backup/stage3_risk.csv" "$csv/stage3_risk.csv"
    [[ ! -f "$backup/risk.csv" ]] || cp -a "$backup/risk.csv" "$csv/risk.csv"
    (( was_active == 0 )) || systemctl restart "$service" || true
  fi
  exit "$rc"
}
trap rollback EXIT

# Exact preserved five-trade settings.
cp -a "$snap/runtime.csv" "$csv/runtime.csv"
cp -a "$snap/stage3_risk.csv" "$csv/stage3_risk.csv"
changed=1

# Owner-requested sole sizing change: force all entry sizing bounds to 0.009 SOL.
ROOT="$root" python3 - <<'PY'
import csv
import os
from pathlib import Path

p = Path(os.environ['ROOT']) / 'CSV' / 'runtime.csv'
with p.open('r', encoding='utf-8-sig', newline='') as f:
    rd = csv.DictReader(f)
    fields = list(rd.fieldnames or [])
    rows = list(rd)
if 'key' not in fields or 'value' not in fields:
    raise SystemExit('REFUSE: runtime.csv is not key,value format')

wanted = {
    'auto_probe_sol': '0.009',
    'auto_entry_min_sol': '0.009',
    'auto_entry_max_sol': '0.009',
}
seen = set()
for row in rows:
    key = str(row.get('key') or '').strip()
    if key in wanted:
        row['value'] = wanted[key]
        seen.add(key)
for key, value in wanted.items():
    if key not in seen:
        row = {name: '' for name in fields}
        row['key'] = key
        row['value'] = value
        if 'notes' in fields:
            row['notes'] = 'Owner-approved SiRisky entry size 0.009 SOL'
        rows.append(row)

tmp = p.with_suffix('.csv.tmp')
with tmp.open('w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, p)
PY

# Verify runtime matches the preserved five-trade snapshot except the three size keys.
ROOT="$root" SNAP="$snap" python3 - <<'PY'
import csv
import os
from pathlib import Path

size_keys = {'auto_probe_sol', 'auto_entry_min_sol', 'auto_entry_max_sol'}

def read(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as f:
        return {str(r.get('key') or '').strip(): str(r.get('value') or '').strip() for r in csv.DictReader(f)}

snap = read(Path(os.environ['SNAP']) / 'runtime.csv')
cur = read(Path(os.environ['ROOT']) / 'CSV' / 'runtime.csv')
for key, value in snap.items():
    if key in size_keys:
        continue
    if cur.get(key) != value:
        raise SystemExit(f'REFUSE_VERIFY: non-size runtime changed {key}: {cur.get(key)!r} != {value!r}')
extra = set(cur) - set(snap) - size_keys
if extra:
    raise SystemExit(f'REFUSE_VERIFY: unexpected runtime keys: {sorted(extra)}')
for key in size_keys:
    if cur.get(key) != '0.009':
        raise SystemExit(f'REFUSE_VERIFY: {key}={cur.get(key)!r}')

required = {
    'live_enabled': '1',
    'broadcast_enabled': '1',
    'telegram_manual_run_enabled': '0',
    'poll_seconds': '5',
    'auto_discovery_enabled': '1',
    'auto_promote_to_selected': '1',
    'manual_approval_enabled': '0',
    'manual_approval_ttl_seconds': '60',
    'manual_approval_require_external_signature': '0',
    'armed_manual_approval': '0',
    'auto_evaluate_candidate_limit': '1',
    'trading_mode': 'LIVE',
    'paper_auto_trade_enabled': '0',
}
for key, value in required.items():
    if cur.get(key) != value:
        raise SystemExit(f'REFUSE_VERIFY: historical runtime {key}={cur.get(key)!r}, expected {value!r}')
print('runtime_matches_five_trade_snapshot_except_size=true')
PY

cmp -s "$snap/stage3_risk.csv" "$csv/stage3_risk.csv"
echo 'stage3_risk_matches_five_trade_snapshot=true'

systemctl restart "$service"
sleep 10
[[ $(systemctl is-active "$service") == active ]] || { echo 'REFUSE_VERIFY: sirisky.service not active'; exit 45; }

cd "$root"
PYTHONPATH=. .venv/bin/python - <<'PY'
from decimal import Decimal
from sirisky.config import Settings
from sirisky.safety_v2 import entry_sol

s = Settings.load()
rt = s.runtime()
rk = s.risk()
size = Decimal(str(entry_sol(s)))
if size != Decimal('0.009'):
    raise SystemExit(f'REFUSE_VERIFY: effective entry size is {size}, not 0.009')

runtime_checks = {
    'trading_mode': 'LIVE',
    'live_enabled': '1',
    'broadcast_enabled': '1',
    'manual_approval_enabled': '0',
    'auto_evaluate_candidate_limit': '1',
}
for key, value in runtime_checks.items():
    if str(rt.get(key)) != value:
        raise SystemExit(f'REFUSE_VERIFY: effective runtime {key}={rt.get(key)!r}, expected {value!r}')

risk_checks = {
    'max_open_positions': '1',
    'min_exit_health_pct': '85',
    'max_round_trip_cost_pct': '8',
    'fast_take_profit_floor_pct': '2.0',
    'fast_take_profit_cap_pct': '5.0',
    'fast_stop_net_pct': '3.0',
    'warm_reversal_pct': '1.5',
    'hot_reversal_pct': '3.0',
    'fast_max_hold_cap_seconds': '300',
    'lp_recent_sell_sim_max_age_sec': '300',
}
for key, value in risk_checks.items():
    got = str(rk.get(key))
    if got == value:
        continue
    try:
        if Decimal(got) == Decimal(value):
            continue
    except Exception:
        pass
    raise SystemExit(f'REFUSE_VERIFY: effective risk {key}={got!r}, expected {value!r}')

print('five_trade_profile_restore=VERIFIED')
print('effective_entry_sol=0.009000000')
print('trading_mode=LIVE')
print('live_enabled=1')
print('broadcast_enabled=1')
print('manual_approval_enabled=0')
print('auto_evaluate_candidate_limit=1')
for key in risk_checks:
    print(f'{key}={rk.get(key)}')
print('only_logical_change=trade_size_0.0005_to_0.009')
print('forced_trade=NO')
PY

echo 'service_active=true'
trap - EXIT
