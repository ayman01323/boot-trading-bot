#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/root/SiRisky
CSV="$ROOT/CSV"
SERVICE=sirisky.service

[[ $(id -u) -eq 0 ]] || { echo 'REFUSE: root required'; exit 40; }
[[ $(hostname) == botgoogle ]] || { echo 'REFUSE: wrong host'; exit 41; }
for f in runtime.csv stage2_strategy.csv stage3_risk.csv; do
  [[ -f "$CSV/$f" ]] || { echo "REFUSE: $f missing"; exit 42; }
done

# Stop before reading/modifying state so no cycle can race the restore.
systemctl stop "$SERVICE" || true

open_rows=0
if [[ -f "$CSV/open_positions.csv" ]]; then
  open_rows=$(tail -n +2 "$CSV/open_positions.csv" | sed '/^[[:space:]]*$/d' | wc -l)
fi
echo "pre_restore_open_positions=$open_rows"
if [[ "$open_rows" -ne 0 ]]; then
  systemctl start "$SERVICE" || true
  echo 'REFUSE: open SiRisky position exists; no settings changed'
  exit 45
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$ROOT/data/pre-1240-shadow-v2-$stamp"
mkdir -p "$backup"
cp -a "$CSV/runtime.csv" "$backup/runtime.csv"
cp -a "$CSV/stage2_strategy.csv" "$backup/stage2_strategy.csv"
cp -a "$CSV/stage3_risk.csv" "$backup/stage3_risk.csv"
echo "backup=$backup"

rollback() {
  rc=$?
  if (( rc != 0 )); then
    echo 'RESTORE_FAILED: restoring all three settings files'
    cp -a "$backup/runtime.csv" "$CSV/runtime.csv" || true
    cp -a "$backup/stage2_strategy.csv" "$CSV/stage2_strategy.csv" || true
    cp -a "$backup/stage3_risk.csv" "$CSV/stage3_risk.csv" || true
    systemctl restart "$SERVICE" || true
    echo 'rollback=completed'
  fi
  exit "$rc"
}
trap rollback EXIT

ROOT="$ROOT" python3 - <<'PY'
import csv, os
from pathlib import Path

root=Path(os.environ['ROOT'])
csvdir=root/'CSV'

def kv_read(path):
    rows=[]
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rows=list(csv.reader(f))
    if not rows:
        raise RuntimeError(f'{path.name} empty')
    header=rows[0] if rows[0] and rows[0][0].strip().lower() in {'setting','key'} else None
    body=rows[1:] if header else rows
    return header, body

def atomic_rows(path, rows):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f); w.writerows(rows); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)

# Runtime: retain 12:40 operating semantics but SHADOW-only.
p=csvdir/'runtime.csv'
header,body=kv_read(p)
width=max(3,max((len(r) for r in body if r),default=3))
by={str(r[0]).strip():r for r in body if len(r)>=2 and str(r[0]).strip()}
wanted={
 'trading_mode':'SHADOW',
 'live_enabled':'1',
 'broadcast_enabled':'0',
 'paper_auto_trade_enabled':'1',
 'telegram_manual_run_enabled':'0',
 'poll_seconds':'5',
 'single_position_only':'1',
 'single_cycle_only':'1',
 'auto_apply_stage8_updates':'0',
 'auto_discovery_enabled':'1',
 'auto_probe_sol':'0.09',
 'auto_promote_to_selected':'1',
 'auto_evaluate_candidate_limit':'5',
 'manual_approval_enabled':'0',
 'armed_manual_approval':'0',
 'manual_approval_ttl_seconds':'60',
 'manual_approval_require_external_signature':'0',
 'auto_entry_min_sol':'0.09',
 'auto_entry_max_sol':'0.09',
}
for k,v in wanted.items():
    r=by.get(k)
    if r is None:
        r=['']*width; r[0]=k; body.append(r); by[k]=r
    while len(r)<width: r.append('')
    r[1]=v
    if width>=3: r[2]='SiRisky 12:40 profile restored in SHADOW; real broadcast disabled'
atomic_rows(p,([header] if header else [])+body)

# Stage 2 uses entry_trigger on the live server.
p=csvdir/'stage2_strategy.csv'
with p.open('r',encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); fields=list(rd.fieldnames or []); current=list(rd)
trigger_field='entry_trigger' if 'entry_trigger' in fields else ('trigger' if 'trigger' in fields else '')
required={'strategy_id','age_class','temperature','position_sol','gross_target_pct','execution_buffer_pct','max_hold_seconds','enabled'}
if not trigger_field or not required.issubset(fields):
    raise RuntimeError(f'unexpected stage2 fields: {fields}')
profiles={
 'HR_NEW_01':      ('NEW','COLD','MANUAL_READY','0.09','15.0','0.35','90','1'),
 'HR_NEW_WARM_01': ('NEW','WARM','MANUAL_READY','0.00025','2.0','0.50','60','0'),
 'HR_EARLY_01':    ('EARLY','COLD','MANUAL_READY','0.0005','3.0','0.35','300','0'),
 'HR_EST_01':      ('ESTABLISHED','COLD','MANUAL_READY','0.0005','2.5','0.35','600','0'),
}
out=[]
for sid,(age,temp,trig,pos,gross,buf,hold,en) in profiles.items():
    row=next((dict(r) for r in current if str(r.get('strategy_id') or '').strip()==sid),{})
    row.update({
      'strategy_id':sid,'age_class':age,'temperature':temp,trigger_field:trig,
      'position_sol':pos,'gross_target_pct':gross,'execution_buffer_pct':buf,
      'max_hold_seconds':hold,'enabled':en,
    })
    if 'notes' in fields:
        row['notes']='12:40 strategy table; HR_NEW_01 requested 15% gross target and 0.09 SOL SHADOW size' if sid=='HR_NEW_01' else '12:40 disabled strategy row'
    for fld in fields: row.setdefault(fld,'')
    out.append(row)
tmp=p.with_suffix('.csv.tmp')
with tmp.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out); f.flush(); os.fsync(f.fileno())
os.replace(tmp,p)

# Stage 3: exact 12:40 risk values. 15% Stage-2 gross target does NOT alter the 5% fast TP cap.
p=csvdir/'stage3_risk.csv'
existing=[]
with p.open('r',encoding='utf-8-sig',newline='') as f: existing=list(csv.reader(f))
header=existing[0] if existing and existing[0] and existing[0][0].strip().lower() in {'setting','key'} else ['setting','value','notes']
width=max(3,len(header))
vals=[
 ('max_open_positions','1'),('min_exit_health_pct','85'),('min_forecast_net_pct','0.25'),
 ('max_round_trip_cost_pct','8'),('untouched_sol_reserve','0.005'),
 ('fast_take_profit_floor_pct','2.0'),('fast_take_profit_cap_pct','5.0'),
 ('fast_stop_net_pct','3.0'),('warm_reversal_pct','1.5'),('hot_reversal_pct','3.0'),
 ('fast_max_hold_cap_seconds','300'),('lp_recent_sell_sim_max_age_sec','300'),
]
out=[header]
for k,v in vals:
    r=['']*width; r[0]=k; r[1]=v; r[2]='12:40 SiRisky risk profile'; out.append(r)
atomic_rows(p,out)
PY

systemctl restart "$SERVICE"
sleep 8
systemctl is-active --quiet "$SERVICE"

cd "$ROOT"
PYTHONPATH=. .venv/bin/python - <<'PY'
import csv
from pathlib import Path
from sirisky.config import Settings

s=Settings.load(); rt=s.runtime(); rk=s.risk()
expected_rt={
 'trading_mode':'SHADOW','live_enabled':'1','broadcast_enabled':'0','paper_auto_trade_enabled':'1',
 'telegram_manual_run_enabled':'0','poll_seconds':'5','single_position_only':'1','single_cycle_only':'1',
 'auto_apply_stage8_updates':'0','auto_discovery_enabled':'1','auto_probe_sol':'0.09',
 'auto_promote_to_selected':'1','auto_evaluate_candidate_limit':'5','manual_approval_enabled':'0',
 'armed_manual_approval':'0','manual_approval_ttl_seconds':'60','manual_approval_require_external_signature':'0',
 'auto_entry_min_sol':'0.09','auto_entry_max_sol':'0.09',
}
for k,v in expected_rt.items():
    if str(rt.get(k))!=v: raise SystemExit(f'VERIFY_FAIL runtime {k}={rt.get(k)!r} expected {v!r}')
expected_risk={
 'max_open_positions':'1','min_exit_health_pct':'85','min_forecast_net_pct':'0.25','max_round_trip_cost_pct':'8',
 'untouched_sol_reserve':'0.005','fast_take_profit_floor_pct':'2.0','fast_take_profit_cap_pct':'5.0',
 'fast_stop_net_pct':'3.0','warm_reversal_pct':'1.5','hot_reversal_pct':'3.0',
 'fast_max_hold_cap_seconds':'300','lp_recent_sell_sim_max_age_sec':'300',
}
for k,v in expected_risk.items():
    got=str(rk.get(k))
    if got==v: continue
    try:
        if float(got)==float(v): continue
    except Exception: pass
    raise SystemExit(f'VERIFY_FAIL risk {k}={got!r} expected {v!r}')
with (Path('CSV')/'stage2_strategy.csv').open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f))
r=next(x for x in rows if x.get('strategy_id')=='HR_NEW_01')
assert r['position_sol']=='0.09',r
assert r['gross_target_pct']=='15.0',r
assert r['execution_buffer_pct']=='0.35',r
assert r['max_hold_seconds']=='90',r
assert r['enabled']=='1',r
assert sum(str(x.get('enabled') or '0')=='1' for x in rows)==1
print('restore_verified=true')
print('mode=SHADOW')
print('broadcast_enabled=0')
print('strategy=HR_NEW_01')
print('simulated_position_sol=0.09')
print('gross_target_pct=15.0')
print('execution_buffer_pct=0.35')
print('strategy_max_hold_seconds=90')
print('candidate_limit=5')
print('max_open_positions=1')
print('min_exit_health_pct=85')
print('max_round_trip_cost_pct=8')
print('fast_take_profit_floor_pct=2.0')
print('fast_take_profit_cap_pct=5.0')
print('fast_stop_net_pct=3.0')
print('real_money_broadcast=DISABLED')
PY

echo 'service_active=true'
trap - EXIT
