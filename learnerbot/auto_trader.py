from __future__ import annotations

import csv
import os
import time
from decimal import Decimal
from pathlib import Path

from .config import load_kv_scoped
from .live_executor import LiveTrader
from .multi_wallet_store import MultiWalletStore
from .user_registry import all_users, user_bool, user_setting, require_user
from .fee_engine import user_fee_plan, master_wallet, profit_share_amount, ledger
from .execution_quarantine import quarantine_state, route_or_token_blocked, record_execution_mismatch
from .product_universe import route_product_policy


def _bool(v, default=False):
    if v is None: return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}

def _dec(v, default="0"):
    try: return Decimal(str(v))
    except Exception: return Decimal(str(default))

def _rows(path: Path):
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:return list(csv.DictReader(f))

def _append(path: Path, row: dict):
    headers=["timestamp_epoch","telegram_id","wallet_id","chain_id","chain_slug","route_id","route_path","input_base","expected_gross_base","expected_gas_base","expected_net_base","realised_net_base","profit_fee_base","fee_tx_hash","tx_hash","status","note"]
    rows=_rows(path);rows.append(row);rows=rows[-10000:];path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows([{h:r.get(h,"") for h in headers} for r in rows]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def _append_simulation(csv_dir: Path, row: dict):
    """Append a bounded no-broadcast wallet simulation audit row."""
    path=Path(csv_dir)/"auto"/"auto_trade_simulations.csv"
    headers=["timestamp_epoch","telegram_id","wallet_id","chain_id","chain_slug","route_id","route_path","input_base","min_net_profit_base","gross_profit_base","gas_cost_base","simulation_ok","reason"]
    rows=_rows(path);rows.append(row);rows=rows[-10000:];path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows([{h:r.get(h,"") for h in headers} for r in rows]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def auto_state(csv_dir: Path, telegram_id=None) -> dict:
    rows=_rows(Path(csv_dir)/"auto"/"auto_trade_execution.csv");now=int(time.time())
    if telegram_id is not None: rows=[r for r in rows if str(r.get("telegram_id","")).strip()==str(telegram_id)]
    recent=[r for r in rows if now-int(float(r.get("timestamp_epoch") or 0))<=3600]
    broadcasts=[r for r in recent if r.get("status") in {"BROADCAST","SUCCESS","SUCCESS_FEE_PENDING","FAILED","FEE_PENDING"}]
    gas=sum(_dec(r.get("expected_gas_base"),0) for r in broadcasts);last_ts=max([int(float(r.get("timestamp_epoch") or 0)) for r in rows] or [0])
    return {"hour_trades":len(broadcasts),"hour_expected_gas":gas,"last_ts":last_ts,"recent":rows[-10:]}


def _required_pre_fee_min(plan: dict, requested_user_net: Decimal) -> Decimal:
    """Convert the user's retained-net floor to a pre-platform-fee floor.

    A zero-profit-share account must not inherit a fee-settlement gas reserve.
    The old behaviour added the reserve even to MASTER/STANDARD 0% plans, which
    could make tiny test trades mathematically impossible.
    """
    bps=_dec(plan.get("profit_share_bps") or 0);fraction=bps/Decimal(10000)
    if fraction<=0:return requested_user_net
    if fraction>=Decimal("0.95"):fraction=Decimal("0.95")
    settlement=max(Decimal(0),_dec(plan.get("fee_settlement_gas_reserve_base") or "0"))
    return (requested_user_net+settlement)/(Decimal(1)-fraction)


def _user_exec_config(app, tid, chain_id):
    global_cfg=load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",chain_id)
    def us(name,default):return user_setting(app.csv_dir,tid,chain_id,name,default)
    return {
        "auto_on": user_bool(app.csv_dir,tid,chain_id,"auto_trading_enabled",False),
        "live_on": user_bool(app.csv_dir,tid,chain_id,"live_trading_enabled",False),
        "mode": str(us("recommendation_mode","SHADOW")).upper(),
        "input": _dec(us("auto_input_base",global_cfg.get("auto_input_base","0.005")),"0.005"),
        "max_input": _dec(us("max_auto_input_base",global_cfg.get("max_auto_input_base","0.05")),"0.05"),
        "min_user_net": _dec(us("min_net_profit_base",global_cfg.get("min_net_profit_base","0.0002")),"0.0002"),
        # min_net_profit_base is a flat amount in the chain's native/base token, so
        # the same default protects very differently across chains (e.g. 0.0002 WETH
        # is meaningful, 0.0002 WMATIC is economically negligible). Require net
        # profit to also clear a multiple of the actual estimated gas cost, which
        # scales correctly per chain since gas is already denominated in that
        # chain's native token.
        "min_gas_multiple": _dec(us("direct_market_min_gas_multiple",global_cfg.get("direct_market_min_gas_multiple","2.0")),"2.0"),
        "max_hour": max(1,int(float(us("max_auto_trades_per_hour",global_cfg.get("max_auto_trades_per_hour","5"))))),
        "cooldown": max(1,int(float(us("cooldown_seconds",global_cfg.get("cooldown_seconds","20"))))),
        "max_gas_hour": _dec(us("max_auto_expected_gas_per_hour_base",global_cfg.get("max_auto_expected_gas_per_hour_base","0.02")),"0.02"),
    }


def _candidate_score(r):
    return _dec(r.get("expected_gross_profit_base"),0)-_dec(r.get("slippage_reserve_base"),0)


def _meets_gas_multiple_floor(sim: dict, min_gas_multiple) -> bool:
    """Require net profit to clear a multiple of the actual estimated gas cost.

    min_net_profit_base alone is a flat native-token amount, so the same default
    protects very differently across chains (0.0002 WETH vs. 0.0002 WMATIC). Gas
    cost is already denominated correctly per chain, so scaling the floor off it
    is chain-appropriate without needing a separate per-chain override.
    """
    gas_cost=_dec(sim.get("gas_cost_base"),0)
    if gas_cost<=0:return True
    net_over_gas=_dec(sim.get("gross_profit"),0)-gas_cost
    return net_over_gas>=gas_cost*_dec(min_gas_multiple,"2.0")


def _eligible_scanner_candidate(r: dict) -> bool:
    """Require every scanner-side safety gate explicitly before wallet simulation.

    Missing fields fail closed.  This prevents stale/diagnostic/rejected rows from
    reaching the per-user simulation and signing path merely because a historical
    CSV used permissive defaults.
    """
    required_true = (
        "enabled",
        "scanner_exact",
        "source_verified",
        "exact_quote_ok",
        "liquidity_ok",
        "route_approved",
        "whole_route_approved",
    )
    return all(_bool(r.get(k), False) for k in required_true)


def execute_best_live_opportunity(app, opportunities: list[dict]) -> list[dict]:
    """Execute at most one fresh route per eligible user per cycle.

    The platform switch in auto_trading_settings.csv is a global emergency/admin gate.
    Every Telegram user then needs their own LIVE + ARMED + AUTOTRADE settings.
    """
    platform=load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",0)
    if not _bool(platform.get("auto_trading_enabled","false"),False):return []
    store=MultiWalletStore(app.data_dir,app.csv_dir);events=[];now=int(time.time())
    base_candidates=[r for r in opportunities if _eligible_scanner_candidate(r)]
    qstates={}
    def not_quarantined(r):
        cid=str(r.get("chain_id") or "")
        if cid not in qstates:qstates[cid]=quarantine_state(app.csv_dir,cid,now)
        path=[x for x in str(r.get("route_path") or "").split(">") if x]
        blocked,_=route_or_token_blocked(qstates[cid],str(r.get("route_id") or ""),path)
        return not blocked
    base_candidates=[r for r in base_candidates if not_quarantined(r)]
    def product_approved(r):
        path=[x for x in str(r.get("route_path") or "").split(">") if x]
        policy=route_product_policy(app.csv_dir,r.get("chain_id") or "",path)
        return bool(policy.get("auto_trade"))
    base_candidates=[r for r in base_candidates if product_approved(r)]
    base_candidates.sort(key=_candidate_score,reverse=True)
    for u in all_users(app.csv_dir,enabled_only=True):
        if (u.get("status") or "").upper()!="ACTIVE" or not _bool(u.get("can_auto_trade"),True):continue
        tid=str(u.get("telegram_id") or "").strip()
        if not tid or not store.has_wallet(tid):continue
        try:meta=store.get_meta(tid)
        except Exception:continue
        # Choose the strongest route that passes this user's limits and wallet-specific simulation.
        chosen=None;chosen_sim=None;chosen_cfg=None;chosen_trader=None;chosen_plan=None
        for r in base_candidates:
            chain_slug=str(r.get("chain_slug") or "").strip().lower()
            if not chain_slug:continue
            route_kind=str(r.get("route_kind") or "V2_CYCLE").upper()
            if route_kind.startswith("CROSS_") or str(r.get("execution_mode") or "").upper().startswith("SHADOW"):
                continue
            try:
                require_user(app.csv_dir,tid,active=True,chain_slug=chain_slug)
                trader=LiveTrader(app,chain_slug,telegram_id=tid,wallet_id=meta["wallet_id"],router_override=((r.get("router_address") or None) if route_kind=="V2_CYCLE" else None))
            except Exception:continue
            cfg=_user_exec_config(app,tid,trader.chain.chain_id)
            if not (cfg["auto_on"] and cfg["live_on"] and cfg["mode"]=="ARMED"):continue
            st=auto_state(app.csv_dir,tid)
            if st["hour_trades"]>=cfg["max_hour"] or st["hour_expected_gas"]>=cfg["max_gas_hour"] or now-st["last_ts"]<cfg["cooldown"]:continue
            amount=min(cfg["input"],cfg["max_input"])
            if amount<=0:continue
            plan=user_fee_plan(app.csv_dir,tid) or {}
            if _dec(plan.get("profit_share_bps") or 0)>0 and not master_wallet(app.csv_dir,trader.chain.chain_id):
                continue
            pre_fee_min=_required_pre_fee_min(plan,cfg["min_user_net"])
            path=[x for x in (r.get("route_path") or "").split(">") if x]
            try:
                if route_kind=="V3_CYCLE":
                    fees=[int(x) for x in str(r.get("route_fees") or "").split(">") if str(x).strip()]
                    sim=trader.simulate_v3_cycle(path,fees,amount,pre_fee_min,r.get("router_address"),r.get("quoter_address"))
                else:
                    sim=trader.simulate_cycle(path,amount,pre_fee_min)
                _append_simulation(app.csv_dir,{
                    "timestamp_epoch":int(time.time()),"telegram_id":tid,"wallet_id":meta.get("wallet_id",""),
                    "chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,"route_id":r.get("route_id",""),
                    "route_path":r.get("route_path",""),"input_base":str(amount),"min_net_profit_base":str(pre_fee_min),
                    "gross_profit_base":str(sim.get("gross_profit",0)),"gas_cost_base":str(sim.get("gas_cost_base",0)),
                    "simulation_ok":str(bool(sim.get("simulation_ok"))).lower(),"reason":str(sim.get("reason") or "")[:500],
                })
            except Exception as exc:
                _append_simulation(app.csv_dir,{
                    "timestamp_epoch":int(time.time()),"telegram_id":tid,"wallet_id":meta.get("wallet_id",""),
                    "chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,"route_id":r.get("route_id",""),
                    "route_path":r.get("route_path",""),"input_base":str(amount),"min_net_profit_base":str(pre_fee_min),
                    "gross_profit_base":"","gas_cost_base":"","simulation_ok":"false",
                    "reason":f"{type(exc).__name__}: {str(exc)[:450]}",
                })
                continue
            if not sim.get("simulation_ok"):
                if route_kind=="V2_CYCLE":
                    record_execution_mismatch(app.csv_dir,trader.chain.chain_id,r.get("route_id", ""),path,str(sim.get("reason") or ""))
                continue
            if not _meets_gas_multiple_floor(sim,cfg["min_gas_multiple"]):
                continue
            chosen=(r,path,amount,pre_fee_min,route_kind);chosen_sim=sim;chosen_cfg=cfg;chosen_trader=trader;chosen_plan=plan;break
        if not chosen:continue
        r,path,amount,pre_fee_min,route_kind=chosen;trader=chosen_trader;plan=chosen_plan;log_path=Path(app.csv_dir)/"auto"/"auto_trade_execution.csv"
        before_wrapped=trader.wrapped_balance()
        try:
            if route_kind=="V3_CYCLE":
                fees=[int(x) for x in str(r.get("route_fees") or "").split(">") if str(x).strip()]
                result=trader.execute_v3_cycle(path,fees,amount,pre_fee_min,r.get("router_address"),r.get("quoter_address"),"CONFIRM")
            else:
                result=trader.execute_cycle(path,amount,pre_fee_min,"CONFIRM")
            txh=result["tx_hash"]
            try:receipt=trader.w3.eth.wait_for_transaction_receipt(txh,timeout=180,poll_latency=2)
            except Exception as exc:raise RuntimeError(f"cycle broadcast {txh} but receipt timed out") from exc
            if int(receipt.status)!=1:raise RuntimeError(f"cycle transaction failed: {txh}")
            after_wrapped=trader.wrapped_balance();gas_price=int(getattr(receipt,"effectiveGasPrice",0) or receipt.get("effectiveGasPrice",0) or 0);gas_used=int(getattr(receipt,"gasUsed",0) or receipt.get("gasUsed",0) or 0)
            actual_gas=Decimal(gas_price*gas_used)/Decimal(10**18);realised=(after_wrapped-before_wrapped)-actual_gas
            fee=profit_share_amount(app.csv_dir,tid,realised);fee_tx="";master=master_wallet(app.csv_dir,trader.chain.chain_id);fee_status="NONE";fee_note=""
            if fee>0 and master:
                raw=int(fee*Decimal(10**18))
                if raw>0:
                    try:
                        fee_tx=trader.transfer_wrapped_raw(master,raw);fee_status="BROADCAST"
                        ledger(app.csv_dir,{"telegram_id":tid,"wallet_id":meta["wallet_id"],"chain_id":trader.chain.chain_id,"fee_type":"PROFIT_SHARE","plan_id":u.get("fee_plan_id") or "","gross_profit_base":str(after_wrapped-before_wrapped),"gas_cost_base":str(actual_gas),"net_profit_base":str(realised),"fee_amount_base":str(fee),"fee_asset":trader.chain.wrapped_base_symbol,"master_address":master,"tx_hash":fee_tx,"status":"BROADCAST","note":"Fee calculated only on positive realised cycle net after actual cycle gas"})
                    except Exception as fee_exc:
                        fee_status="PENDING";fee_note=f"profit fee settlement failed: {type(fee_exc).__name__}: {str(fee_exc)[:200]}"
                        ledger(app.csv_dir,{"telegram_id":tid,"wallet_id":meta["wallet_id"],"chain_id":trader.chain.chain_id,"fee_type":"PROFIT_SHARE","plan_id":u.get("fee_plan_id") or "","gross_profit_base":str(after_wrapped-before_wrapped),"gas_cost_base":str(actual_gas),"net_profit_base":str(realised),"fee_amount_base":str(fee),"fee_asset":trader.chain.wrapped_base_symbol,"master_address":master,"tx_hash":"","status":"PENDING","note":fee_note})
            status="SUCCESS_FEE_PENDING" if fee_status=="PENDING" else "SUCCESS"
            row={"timestamp_epoch":now,"telegram_id":tid,"wallet_id":meta["wallet_id"],"chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,"route_id":r.get("route_id"),"route_path":r.get("route_path"),"input_base":str(amount),"expected_gross_base":str(chosen_sim.get("gross_profit",0)),"expected_gas_base":str(chosen_sim.get("gas_cost_base",0)),"expected_net_base":str(_dec(chosen_sim.get("gross_profit"),0)-_dec(chosen_sim.get("gas_cost_base"),0)),"realised_net_base":str(realised),"profit_fee_base":str(fee),"fee_tx_hash":fee_tx,"tx_hash":txh,"status":status,"note":"user-specific fresh re-quote/simulation + receipt-confirmed realised P&L"+(" | "+fee_note if fee_note else "")}
            _append(log_path,row);events.append(row)
        except Exception as exc:
            row={"timestamp_epoch":now,"telegram_id":tid,"wallet_id":meta.get("wallet_id",""),"chain_id":r.get("chain_id"),"chain_slug":r.get("chain_slug"),"route_id":r.get("route_id"),"route_path":r.get("route_path"),"input_base":str(amount),"expected_gross_base":str(chosen_sim.get("gross_profit",0) if chosen_sim else 0),"expected_gas_base":str(chosen_sim.get("gas_cost_base",0) if chosen_sim else 0),"expected_net_base":"","realised_net_base":"","profit_fee_base":"","fee_tx_hash":"","tx_hash":"","status":"REJECTED","note":f"{type(exc).__name__}: {str(exc)[:500]}"}
            _append(log_path,row);events.append(row)
    return events
