#!/usr/bin/env bash
set -Eeuo pipefail

root=/root/SiRisky
csv="$root/CSV"
service=sirisky.service

[[ $(id -u) -eq 0 ]] || { echo 'REFUSE: root required'; exit 40; }
[[ $(hostname) == botgoogle ]] || { echo 'REFUSE: wrong host'; exit 41; }
[[ -f "$csv/runtime.csv" ]] || { echo 'REFUSE: runtime.csv missing'; exit 42; }
[[ -f "$csv/stage2_strategy.csv" ]] || { echo 'REFUSE: stage2_strategy.csv missing'; exit 43; }
[[ -f "$csv/stage3_risk.csv" ]] || { echo 'REFUSE: stage3_risk.csv missing'; exit 44; }

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
  echo 'REFUSE: open SiRisky position exists; no settings changed'
  exit 45
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$root/data/pre-1240-shadow-15pct-0090-$stamp"
mkdir -p "$backup"
cp -a "$csv/runtime.csv" "$backup/runtime.csv"
cp -a "$csv/stage2_strategy.csv" "$backup/stage2_strategy.csv"
cp -a "$csv/stage3_risk.csv" "$backup/stage3_risk.csv"
echo "backup=$backup"

changed=0
rollback() {
  rc=$?
  if [[ "$rc" -ne 0 && "$changed" -eq 1 ]]; then
    cp -a "$backup/runtime.csv" "$csv/runtime.csv" || true
    cp -a "$backup/stage2_strategy.csv" "$csv/stage2_strategy.csv" || true
    cp -a "$backup/stage3_risk.csv" "$csv/stage3_risk.csv" || true
    (( was_active == 0 )) || systemctl restart "$service" || true
    echo 'rollback=completed'
  fi
  exit "$rc"
}
trap rollback EXIT

ROOT="$root" python3 - <<'PY'
import csv
import os
from pathlib import Path

root = Path(os.environ['ROOT'])
csvdir = root / 'CSV'

def read_csv_rows(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.reader(f))

def atomic_write_rows(path, rows):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

# Runtime: reproduce the 12:40 operational profile but force SHADOW/no broadcast.
p = csvdir / 'runtime.csv'
rows = read_csv_rows(p)
if not rows:
    raise SystemExit('runtime.csv empty')
header = rows[0] if rows[0] and rows[0][0].strip().lower() in {'setting', 'key'} else None
body = rows[1:] if header else rows
width = max(3, max((len(r) for r in body if r), default=3))
by = {str(r[0]).strip(): r for r in body if len(r) >= 2 and str(r[0]).strip()}
wanted = {
    'trading_mode': ('SHADOW', 'Safety restore: SHADOW only'),
    'live_enabled': ('1', 'Live market data enabled'),
    'broadcast_enabled': ('0', 'Safety lock: real transaction broadcast disabled'),
    'paper_auto_trade_enabled': ('1', 'Automatic Stage 1-8 SHADOW lifecycle'),
    'telegram_manual_run_enabled': ('0', 'Telegram direct execution disabled'),
    'poll_seconds': ('5', '12:40 poll interval'),
    'single_position_only': ('1', '12:40 single-position profile'),
    'single_cycle_only': ('1', '12:40 single-cycle profile'),
    'auto_apply_stage8_updates': ('0', 'Stage 8 does not auto-apply'),
    'auto_discovery_enabled': ('1', 'Continuous Solana discovery'),
    'auto_probe_sol': ('0.09', 'Requested SHADOW size'),
    'auto_promote_to_selected': ('1', 'Feed ranked candidates into Stage 2/3'),
    'auto_evaluate_candidate_limit': ('5', '12:40 five-candidate evaluation'),
    'manual_approval_enabled': ('0', 'Not required for SHADOW'),
    'armed_manual_approval': ('0', 'Manual approval not armed'),
    'manual_approval_ttl_seconds': ('60', '12:40 TTL'),
    'manual_approval_require_external_signature': ('0', 'No signing occurs in SHADOW'),
    'auto_entry_min_sol': ('0.09', 'Requested SHADOW minimum entry'),
    'auto_entry_max_sol': ('0.09', 'Pinned for deterministic SHADOW verification'),
}
for key, (value, note) in wanted.items():
    row = by.get(key)
    if row is None:
        row = [''] * width
        row[0] = key
        body.append(row)
        by[key] = row
    while len(row) < width:
        row.append('')
    row[1] = value
    if width >= 3:
        row[2] = note
atomic_write_rows(p, ([header] if header else []) + body)

# Stage 2: exact 12:40 strategy table, with only requested size/target changes.
p = csvdir / 'stage2_strategy.csv'
with p.open('r', encoding='utf-8-sig', newline='') as f:
    rd = csv.DictReader(f)
    fields = list(rd.fieldnames or [])
    current = list(rd)
required = {'strategy_id','age_class','temperature','trigger','position_sol','gross_target_pct','execution_buffer_pct','max_hold_seconds','enabled'}
if not required.issubset(fields):
    raise SystemExit(f'unexpected stage2 fields: {fields}')
profiles = {
    'HR_NEW_01':      {'age_class':'NEW','temperature':'COLD','trigger':'MANUAL_READY','position_sol':'0.09','gross_target_pct':'15.0','execution_buffer_pct':'0.35','max_hold_seconds':'90','enabled':'1'},
    'HR_NEW_WARM_01': {'age_class':'NEW','temperature':'WARM','trigger':'MANUAL_READY','position_sol':'0.00025','gross_target_pct':'2.0','execution_buffer_pct':'0.50','max_hold_seconds':'60','enabled':'0'},
    'HR_EARLY_01':    {'age_class':'EARLY','temperature':'COLD','trigger':'MANUAL_READY','position_sol':'0.0005','gross_target_pct':'3.0','execution_buffer_pct':'0.35','max_hold_seconds':'300','enabled':'0'},
    'HR_EST_01':      {'age_class':'ESTABLISHED','temperature':'COLD','trigger':'MANUAL_READY','position_sol':'0.0005','gross_target_pct':'2.5','execution_buffer_pct':'0.35','max_hold_seconds':'600','enabled':'0'},
}
out = []
for sid, vals in profiles.items():
    row = next((dict(r) for r in current if str(r.get('strategy_id') or '').strip() == sid), {})
    row['strategy_id'] = sid
    row.update(vals)
    for field in fields:
        row.setdefault(field, '')
    out.append(row)
tmp = p.with_suffix('.csv.tmp')
with tmp.open('w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(out)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, p)

# Stage 3: exact 12:40 risk values. The 2-5% fast TP band remains unchanged.
p = csvdir / 'stage3_risk.csv'
existing = read_csv_rows(p)
header = existing[0] if existing and existing[0] and existing[0][0].strip().lower() in {'setting','key'} else ['setting','value','notes']
width = max(3, len(header))
vals = [
    ('max_open_positions','1','12:40 single-position profile'),
    ('min_exit_health_pct','85','12:40 exit-health floor'),
    ('min_forecast_net_pct','0.25','12:40 minimum forecast net'),
    ('max_round_trip_cost_pct','8','12:40 round-trip ceiling'),
    ('untouched_sol_reserve','0.005','12:40 untouched SOL reserve'),
    ('fast_take_profit_floor_pct','2.0','12:40 fast TP floor'),
    ('fast_take_profit_cap_pct','5.0','12:40 fast TP cap'),
    ('fast_stop_net_pct','3.0','12:40 fast stop'),
    ('warm_reversal_pct','1.5','12:40 warm reversal'),
    ('hot_reversal_pct','3.0','12:40 hot reversal'),
    ('fast_max_hold_cap_seconds','300','12:40 hard hold cap'),
    ('lp_recent_sell_sim_max_age_sec','300','12:40 sell evidence age'),
]
out = [header]
for key, value, note in vals:
    row = [''] * width
    row[0] = key
    row[1] = value
    row[2] = note
    out.append(row)
atomic_write_rows(p, out)
PY
changed=1

systemctl restart "$service"
sleep 8
systemctl is-active --quiet "$service"

cd "$root"
PYTHONPATH=. .venv/bin/python - <<'PY'
import csv
from pathlib import Path
from sirisky.config import Settings

s = Settings.load()
rt = s.runtime()
rk = s.risk()
runtime_checks = {
    'trading_mode':'SHADOW', 'live_enabled':'1', 'broadcast_enabled':'0', 'paper_auto_trade_enabled':'1',
    'telegram_manual_run_enabled':'0', 'poll_seconds':'5', 'single_position_only':'1', 'single_cycle_only':'1',
    'auto_apply_stage8_updates':'0', 'auto_discovery_enabled':'1', 'auto_probe_sol':'0.09',
    'auto_promote_to_selected':'1', 'auto_evaluate_candidate_limit':'5', 'manual_approval_enabled':'0',
    'armed_manual_approval':'0', 'manual_approval_ttl_seconds':'60', 'manual_approval_require_external_signature':'0',
    'auto_entry_min_sol':'0.09', 'auto_entry_max_sol':'0.09',
}
for key, expected in runtime_checks.items():
    got = str(rt.get(key))
    if got != expected:
        raise SystemExit(f'VERIFY_FAIL runtime {key}={got!r} expected {expected!r}')
risk_checks = {
    'max_open_positions':'1','min_exit_health_pct':'85','min_forecast_net_pct':'0.25','max_round_trip_cost_pct':'8',
    'untouched_sol_reserve':'0.005','fast_take_profit_floor_pct':'2.0','fast_take_profit_cap_pct':'5.0',
    'fast_stop_net_pct':'3.0','warm_reversal_pct':'1.5','hot_reversal_pct':'3.0',
    'fast_max_hold_cap_seconds':'300','lp_recent_sell_sim_max_age_sec':'300',
}
for key, expected in risk_checks.items():
    got = str(rk.get(key))
    if got == expected:
        continue
    try:
        if float(got) == float(expected):
            continue
    except Exception:
        pass
    raise SystemExit(f'VERIFY_FAIL risk {key}={got!r} expected {expected!r}')
with (Path('CSV') / 'stage2_strategy.csv').open(encoding='utf-8-sig', newline='') as f:
    rows = list(csv.DictReader(f))
row = next(r for r in rows if r.get('strategy_id') == 'HR_NEW_01')
assert row['position_sol'] == '0.09', row
assert row['gross_target_pct'] == '15.0', row
assert row['execution_buffer_pct'] == '0.35', row
assert row['max_hold_seconds'] == '90', row
assert row['enabled'] == '1', row
assert sum(str(r.get('enabled') or '0') == '1' for r in rows) == 1
print('restore_verified=true')
print('mode=SHADOW')
print('broadcast_enabled=0')
print('strategy=HR_NEW_01')
print('simulated_position_sol=0.09')
print('gross_target_pct=15.0')
print('execution_buffer_pct=0.35')
print('strategy_max_hold_seconds=90')
print('candidate_limit=5')
print('fast_take_profit_floor_pct=2.0')
print('fast_take_profit_cap_pct=5.0')
print('real_money_broadcast=DISABLED')
PY

echo 'service_active=true'
trap - EXIT
