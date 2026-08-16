#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CSV=ROOT/'CSVbot'
SUPPORTED={'1','56','137','8453','42161'}

def read(path):
    if not path.exists(): return [],[]
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f); return list(r),list(r.fieldnames or [])

def write(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])
    tmp.replace(path)

def force_scoped_off(path,setting):
    rows,fields=read(path)
    if not fields: fields=['chain_id','setting','value','description']
    target=None
    for r in rows:
        if str(r.get('chain_id') or '*')=='*' and str(r.get('setting') or '')==setting:
            target=r;break
    if target is None:
        target={'chain_id':'*','setting':setting,'value':'false','description':'MASTER safety gate; intentionally OFF after upgrade'}
        rows.append(target)
    target['value']='false'
    write(path,rows,fields)

# Keep every user setting, but ensure all five supported EVM chains stay enabled.
rows,fields=read(CSV/'chains.csv')
for r in rows:
    if str(r.get('chain_id') or '') in SUPPORTED:
        r['enabled']='true'
        note=str(r.get('notes') or '')
        if 'v2.2.1' not in note:
            r['notes']=(note+' | ' if note else '')+'Enabled v2.2.1 final auto'
write(CSV/'chains.csv',rows,fields)

# Upgrades never silently start broadcasting. User enables gates after server preflight.
force_scoped_off(CSV/'live_trading_settings.csv','trading_enabled')
force_scoped_off(CSV/'auto_trading_settings.csv','auto_trading_enabled')

print('v2.2.1 configuration preserved; five EVM chains enabled; MASTER LIVE/AUTO reset OFF')
