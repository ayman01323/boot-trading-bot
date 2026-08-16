from __future__ import annotations

import csv
import os
import time
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from .config import load_chains
from .user_registry import get_user, activate_user


def _bool(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}

def _rows(path):
    p=Path(path)
    if not p.exists():return []
    with p.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))

def fee_plan(csv_dir, plan_id):
    for r in _rows(Path(csv_dir)/"fee_plans.csv"):
        if (r.get("plan_id") or "").strip()==str(plan_id) and _bool(r.get("enabled"),True):return r
    return None

def user_fee_plan(csv_dir,telegram_id):
    u=get_user(csv_dir,telegram_id)
    if not u:return None
    return fee_plan(csv_dir,u.get("fee_plan_id") or "STANDARD")

def master_wallet(csv_dir,chain_id):
    for r in _rows(Path(csv_dir)/"master_wallets.csv"):
        if str(r.get("chain_id","")).strip()==str(chain_id) and _bool(r.get("enabled"),True):
            a=(r.get("address") or "").strip()
            if Web3.is_address(a):return Web3.to_checksum_address(a)
    return None

def _append(path,row,headers,keep=10000):
    path=Path(path);rows=_rows(path);rows.append(row);rows=rows[-keep:];path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows([{h:r.get(h,"") for h in headers} for r in rows]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def ledger(csv_dir,row):
    headers=["timestamp_epoch","telegram_id","wallet_id","chain_id","fee_type","plan_id","gross_profit_base","gas_cost_base","net_profit_base","fee_amount_base","fee_asset","master_address","tx_hash","status","note"]
    row={**row,"timestamp_epoch":row.get("timestamp_epoch") or int(time.time())};_append(Path(csv_dir)/"auto"/"fee_ledger.csv",row,headers)

def profit_share_amount(csv_dir,telegram_id,net_profit_base):
    plan=user_fee_plan(csv_dir,telegram_id) or {};bps=Decimal(str(plan.get("profit_share_bps") or "0"));net=Decimal(str(net_profit_base))
    if net<=0 or bps<=0:return Decimal(0)
    return (net*bps/Decimal(10000)).quantize(Decimal("0.000000000000000001"))

def fixed_activation_fee(csv_dir,telegram_id,chain_slug,app):
    plan=user_fee_plan(csv_dir,telegram_id) or {};chain=next((c for c in load_chains(app,False) if c.slug==chain_slug),None)
    if not chain:raise ValueError("Unknown chain")
    key=f"activation_fee_{chain.slug}"
    raw=(plan.get(key) or plan.get("activation_fee_native") or "0").strip()
    return Decimal(raw),master_wallet(csv_dir,chain.chain_id),chain

def mark_activation_paid(csv_dir,telegram_id,plan_id,chain_id,amount,master,tx_hash):
    activate_user(csv_dir,telegram_id,plan_id,"Activated by fixed on-chain fee")
    ledger(csv_dir,{"telegram_id":telegram_id,"wallet_id":"","chain_id":chain_id,"fee_type":"ACTIVATION","plan_id":plan_id,"gross_profit_base":"","gas_cost_base":"","net_profit_base":"","fee_amount_base":str(amount),"fee_asset":"NATIVE","master_address":master,"tx_hash":tx_hash,"status":"BROADCAST","note":"Activation fee paid; user marked active after broadcast"})
