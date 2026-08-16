from __future__ import annotations

import csv
import hashlib
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from itertools import combinations, product
from pathlib import Path

from web3 import Web3

from .config import load_dex_registry, load_kv_scoped
from .live_executor import LiveTrader
from .market_scanner import _configured_token_seeds, _rows, _graph_triangles, _crawl_factory_pairs, _seed_factory_pairs
from .route_scanner import LIVE_HEADERS, _atomic_write, _bool, _dec, _int, _scanner_input_base
from .execution_quarantine import quarantine_state, route_or_token_blocked
from .product_universe import allowed_product_addresses, route_product_policy

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V3_FACTORY_ABI = [{
    "type":"function","name":"getPool","stateMutability":"view",
    "inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],
    "outputs":[{"name":"pool","type":"address"}],
}]
V3_QUOTER_ABI = [{
    "type":"function","name":"quoteExactInput","stateMutability":"nonpayable",
    "inputs":[{"name":"path","type":"bytes"},{"name":"amountIn","type":"uint256"}],
    "outputs":[
        {"name":"amountOut","type":"uint256"},
        {"name":"sqrtPriceX96AfterList","type":"uint160[]"},
        {"name":"initializedTicksCrossedList","type":"uint32[]"},
        {"name":"gasEstimate","type":"uint256"},
    ],
}]
V2_ROUTER_QUOTE_ABI = [{
    "type":"function","name":"getAmountsOut","stateMutability":"view",
    "inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],
    "outputs":[{"name":"amounts","type":"uint256[]"}],
}]

V3_POOL_HEADERS=["chain_id","chain_slug","dex_name","factory_address","router_address","quoter_address","fee","pool_address","token0","token1","last_seen_epoch"]
POWER_REJECT_HEADERS=["observed_at_epoch","chain_id","chain_slug","route_kind","route_path","stage","reason"]
_lock=threading.Lock()


def _atomic_rows(path:Path, rows:list[dict], headers:list[str]):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows([{h:r.get(h,'') for h in headers} for r in rows]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def encode_v3_path(tokens:list[str], fees:list[int]) -> bytes:
    """Encode the canonical V3 exact-input path: address|uint24 fee|address..."""
    if len(tokens)<2 or len(fees)!=len(tokens)-1:
        raise ValueError('V3 path needs one fee per hop')
    out=bytearray()
    for i,t in enumerate(tokens):
        a=Web3.to_checksum_address(t)
        out.extend(bytes.fromhex(a[2:]))
        if i<len(fees):
            fee=int(fees[i])
            if fee<0 or fee>0xFFFFFF:raise ValueError('V3 fee out of uint24 range')
            out.extend(fee.to_bytes(3,'big'))
    return bytes(out)


def _venues(app, chain_id:int, version:str):
    out=[]
    for r in load_dex_registry(app.csv_dir,chain_id):
        if (r.get('version') or '').strip().upper()!=version.upper():continue
        router=(r.get('router') or '').strip();factory=(r.get('factory') or '').strip()
        if not (Web3.is_address(router) and Web3.is_address(factory)):continue
        q=(r.get('quoter') or '').strip()
        auto=_bool(r.get('auto_execute'), version.upper()=='V2')
        row=dict(r);row.update({'router':Web3.to_checksum_address(router),'factory':Web3.to_checksum_address(factory),'auto_execute':auto})
        if q and Web3.is_address(q):row['quoter']=Web3.to_checksum_address(q)
        out.append(row)
    return out


def _fee_tiers(settings:dict)->list[int]:
    raw=str(settings.get('v3_fee_tiers','100,500,2500,10000'))
    out=[]
    for x in raw.replace(';',',').split(','):
        try:v=int(x.strip())
        except Exception:continue
        if 0<v<=100000 and v not in out:out.append(v)
    return out or [100,500,2500,10000]


def discover_v3_seed_pools_for_context(app,ctx,settings:dict,now:int)->tuple[list[dict],list[dict]]:
    path=Path(app.csv_dir)/'auto'/'v3_pool_registry.csv';all_rows=_rows(path);new=[];rej=[]
    venues=_venues(app,ctx.config.chain_id,'V3')
    if not venues:return [],[]
    max_seeds=max(3,min(12,_int(settings.get('product_max_v3_discovery_tokens_per_chain',settings.get('full_power_max_seed_tokens','8')),8)))
    configured=_configured_token_seeds(app.csv_dir,ctx.config.chain_id)
    dynamic=allowed_product_addresses(app.csv_dir,ctx.config.chain_id,include_shadow=True,max_tokens=max_seeds)
    seeds=[]
    for x in configured+dynamic:
        if Web3.is_address(x):
            a=Web3.to_checksum_address(x)
            if a.lower() not in {z.lower() for z in seeds}:seeds.append(a)
        if len(seeds)>=max_seeds:break
    wrapped=Web3.to_checksum_address(ctx.config.wrapped_base_address)
    ordered=[wrapped]+[Web3.to_checksum_address(x) for x in seeds if x.lower()!=wrapped.lower()]
    fees=_fee_tiers(settings)
    try:trader=LiveTrader(app,ctx.config.slug,require_wallet=False)
    except Exception as exc:return [],[{'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_DISCOVERY','route_path':'','stage':'rpc','reason':f'{type(exc).__name__}:{exc}'}]
    for venue in venues:
        if not venue.get('quoter'):
            rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_DISCOVERY','route_path':'','stage':'config','reason':f"{venue.get('dex_name')}:missing_quoter"});continue
        try:
            factory=trader.w3.eth.contract(address=venue['factory'],abi=V3_FACTORY_ABI)
            if not trader.w3.eth.get_code(venue['factory']):raise RuntimeError('factory_has_no_code')
            if not trader.w3.eth.get_code(venue['router']):raise RuntimeError('router_has_no_code')
            if not trader.w3.eth.get_code(venue['quoter']):raise RuntimeError('quoter_has_no_code')
        except Exception as exc:
            rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_DISCOVERY','route_path':'','stage':'venue','reason':f"{venue.get('dex_name')}:{type(exc).__name__}:{exc}"});continue
        for a,b in combinations(ordered,2):
            for fee in fees:
                try:
                    pool=Web3.to_checksum_address(factory.functions.getPool(a,b,int(fee)).call())
                    if pool.lower()==ZERO_ADDRESS.lower() or not trader.w3.eth.get_code(pool):continue
                except Exception:continue
                row={'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'dex_name':venue.get('dex_name') or 'V3','factory_address':venue['factory'],'router_address':venue['router'],'quoter_address':venue['quoter'],'fee':int(fee),'pool_address':pool,'token0':Web3.to_checksum_address(a),'token1':Web3.to_checksum_address(b),'last_seen_epoch':now}
                new.append(row)
    # Upsert per factory/pool/fee.
    best={}
    for r in all_rows+new:
        key=(str(r.get('chain_id')),str(r.get('factory_address','')).lower(),str(r.get('pool_address','')).lower(),str(r.get('fee')))
        best[key]=r
    with _lock:_atomic_rows(path,list(best.values())[-10000:],V3_POOL_HEADERS)
    return new,rej


def discover_full_power_pools(app,contexts)->dict:
    settings=load_kv_scoped(Path(app.csv_dir)/'auto_trading_settings.csv',0);now=int(time.time());found=0;v2_seen=0;rejected=[]
    for ctx in contexts:
        # Slow/background V2 discovery: this is deliberately outside the hot quote path.
        for venue in _venues(app,ctx.config.chain_id,'V2'):
            try:
                t=LiveTrader(app,ctx.config.slug,require_wallet=False,router_override=venue['router'])
                before=len(_rows(Path(app.csv_dir)/'auto'/'pool_registry.csv'))
                pools=_crawl_factory_pairs(t,venue['factory'],app,settings,now,dex_name=venue.get('dex_name') or 'V2',per_cycle_override=max(1,min(50,_int(settings.get('full_power_v2_discovery_pairs_per_venue','8'),8))))
                seed_settings=dict(settings);seed_settings['direct_market_seed_pair_checks_per_venue']=str(max(0,min(100,_int(settings.get('full_power_v2_seed_pair_checks','18'),18))))
                pools=_seed_factory_pairs(t,venue['factory'],venue.get('dex_name') or 'V2',app,seed_settings,pools,now)
                v2_seen+=max(0,len(pools)-before)
            except Exception as exc:
                rejected.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V2_DISCOVERY','route_path':'','stage':'venue','reason':f"{venue.get('dex_name')}:{type(exc).__name__}:{exc}"})
        rows,rej=discover_v3_seed_pools_for_context(app,ctx,settings,now);found+=len(rows);rejected.extend(rej)
    if rejected:_atomic_rows(Path(app.csv_dir)/'auto'/'power_discovery_rejections.csv',rejected[-500:],POWER_REJECT_HEADERS)
    return {'v3_pools_seen':found,'v2_pools_added':v2_seen,'rejected':len(rejected)}


def _v3_graph_rows(app, chain_id:int, factory:str)->list[dict]:
    return [r for r in _rows(Path(app.csv_dir)/'auto'/'v3_pool_registry.csv') if str(r.get('chain_id'))==str(chain_id) and str(r.get('factory_address','')).lower()==factory.lower()]


def _v3_triangles(pool_rows:list[dict], wrapped:str, max_paths:int)->list[tuple[list[str],list[int]]]:
    wrapped=Web3.to_checksum_address(wrapped);adj=defaultdict(list)
    for r in pool_rows:
        try:a=Web3.to_checksum_address(r['token0']);b=Web3.to_checksum_address(r['token1']);fee=int(r['fee'])
        except Exception:continue
        adj[a.lower()].append((b,fee));adj[b.lower()].append((a,fee))
    out=[];seen=set()
    for a,f1 in adj.get(wrapped.lower(),[]):
        for b,f2 in adj.get(a.lower(),[]):
            if b.lower() in {wrapped.lower(),a.lower()}:continue
            for end,f3 in adj.get(b.lower(),[]):
                if end.lower()!=wrapped.lower():continue
                key=(wrapped.lower(),a.lower(),b.lower(),f1,f2,f3)
                if key in seen:continue
                seen.add(key);out.append(([wrapped,a,b,wrapped],[f1,f2,f3]))
                if len(out)>=max_paths:return out
    return out


def _v3_quote(trader:LiveTrader, quoter_addr:str, path:list[str],fees:list[int],amount:Decimal)->dict:
    raw=int(amount*Decimal(10**18));packed=encode_v3_path(path,fees);q=trader.w3.eth.contract(address=Web3.to_checksum_address(quoter_addr),abi=V3_QUOTER_ABI)
    result=q.functions.quoteExactInput(packed,raw).call();out_raw=int(result[0] if isinstance(result,(list,tuple)) else result)
    return {'amount_in_raw':raw,'amount_out_raw':out_raw,'amount_in':amount,'amount_out':Decimal(out_raw)/Decimal(10**18),'gross_profit':Decimal(out_raw-raw)/Decimal(10**18),'quoter_gas':int(result[3]) if isinstance(result,(list,tuple)) and len(result)>3 else 0}



def _scan_v2_hot_chain(app,ctx,settings,checks_budget:int,routes_budget:int)->tuple[list[dict],list[dict]]:
    """Quote V2 triangles only from the persisted verified pair graph; no factory crawl in hot path."""
    rows=[];rej=[];now=int(time.time());pool_rows=_rows(Path(app.csv_dir)/'auto'/'pool_registry.csv');probe=_scanner_input_base(app,ctx.config.chain_id,settings);max_impact=max(1,min(5000,_int(settings.get('max_price_impact_bps','200'),200)));min_edge=max(Decimal(0),_dec(settings.get('direct_market_min_edge_base','0'),'0'));checked=0;qstate=quarantine_state(app.csv_dir,ctx.config.chain_id,now)
    for venue in _venues(app,ctx.config.chain_id,'V2'):
        try:trader=LiveTrader(app,ctx.config.slug,require_wallet=False,router_override=venue['router'])
        except Exception as exc:
            rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V2_CYCLE','route_path':'','stage':'venue','reason':f"{venue.get('dex_name')}:{type(exc).__name__}:{exc}"});continue
        dynamic=allowed_product_addresses(app.csv_dir,ctx.config.chain_id,include_shadow=True,max_tokens=max(4,_int(settings.get('product_max_scan_tokens_per_chain','60'),60)))
        fallback=_configured_token_seeds(app.csv_dir,ctx.config.chain_id)
        universe=[Web3.to_checksum_address(trader.chain.wrapped_base_address)]
        for x in dynamic+fallback:
            if Web3.is_address(x):
                a=Web3.to_checksum_address(x)
                if a.lower() not in {z.lower() for z in universe}:universe.append(a)
        # Add tokens already connected to wrapped base in this exact venue's verified graph only when
        # the product universe has approved them for scanning.  This stops one bad pair from expanding
        # the hot path without classification.
        wrapped=Web3.to_checksum_address(trader.chain.wrapped_base_address)
        for pr in pool_rows:
            if str(pr.get('chain_id'))!=str(ctx.config.chain_id) or str(pr.get('factory_address','')).lower()!=venue['factory'].lower():continue
            a=str(pr.get('token0') or '');b=str(pr.get('token1') or '')
            x=b if a.lower()==wrapped.lower() else a if b.lower()==wrapped.lower() else ''
            if x and Web3.is_address(x):
                x=Web3.to_checksum_address(x)
                allowed={z.lower() for z in dynamic}
                if (not dynamic or x.lower() in allowed) and x.lower() not in {z.lower() for z in universe}:universe.append(x)
        paths=_graph_triangles(pool_rows,ctx.config.chain_id,venue['factory'],wrapped,universe,max(1,checks_budget-checked))
        if not paths:
            rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V2_CYCLE','route_path':'','stage':'graph','reason':f"{venue.get('dex_name')}:no_complete_v2_triangle_in_persisted_registry"})
        for path in paths:
            if checked>=checks_budget or len(rows)>=routes_budget:break
            text='>'.join(path);rid=hashlib.sha256(f"V2HOT|{ctx.config.chain_id}|{venue['router']}|{text}".encode()).hexdigest()[:20]
            blocked,why=route_or_token_blocked(qstate,rid,path)
            if blocked:
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V2_CYCLE','route_path':text,'stage':'quarantine','reason':why});continue
            product_policy=route_product_policy(app.csv_dir,ctx.config.chain_id,path)
            risk=int(product_policy.get('risk_level') or 9)
            risk_impact=max_impact
            if risk==2:risk_impact=min(risk_impact,max(1,_int(settings.get('product_level2_max_price_impact_bps','150'),150)))
            elif risk>=3:risk_impact=min(risk_impact,max(1,_int(settings.get('product_level3_max_price_impact_bps','75'),75)))
            checked+=1
            try:q=trader.cycle_quote(path,probe)
            except Exception as exc:continue
            if q['gross_profit']<=0:continue
            try:q2=trader.cycle_quote(path,probe*2);ratio=(q2['amount_out']/Decimal(2))/q['amount_out'] if q['amount_out']>0 else Decimal(0);impact=max(Decimal(0),(Decimal(1)-ratio)*Decimal(10000))
            except Exception:continue
            if impact>Decimal(risk_impact):
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V2_CYCLE','route_path':text,'stage':'product_risk','reason':f"risk_level={risk}:price_impact_bps:{impact:.2f}>{risk_impact}"});continue
            slip=max(Decimal(0),q['gross_profit'])*Decimal(trader._slippage_bps())/Decimal(10000);conservative=q['gross_profit']-slip
            if conservative<=min_edge:continue
            rows.append({'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'wallet':'DIRECT_MARKET','behaviour':f"DIRECT_{str(venue.get('dex_name') or 'V2').upper().replace(' ','_')}_V2_TRIANGULAR_ARBITRAGE",'route_id':rid,'route_path':text,'route_kind':'V2_CYCLE','protocol':f"{venue.get('dex_name')}_V2",'router_address':venue['router'],'quoter_address':'','route_fees':'','venue_plan':venue.get('dex_name') or 'V2','execution_mode':'LIVE_ATOMIC_SINGLE_ROUTER','scanner_exact':'true','observed_at_epoch':now,'quote_input_base':f'{probe:f}','source_input_base':f'{probe:f}','quoted_output_base':f"{q['amount_out']:f}",'expected_gross_profit_base':f"{q['gross_profit']:f}",'estimated_gas_base':'0','gas_estimate_units':0,'builder_fee_base':'0','slippage_reserve_base':f'{slip:f}','price_impact_bps':f'{impact:.2f}','copy_score':'','source_verified':'true','exact_quote_ok':'true','simulation_ok':'false','liquidity_ok':'true','sellability_ok':'true','route_approved':'true','whole_route_approved':'true','atomic_profit_protection':'true','canary_complete':'false','enabled':'true' if venue.get('auto_execute',True) and product_policy.get('auto_trade') else 'false','notes':f"full_power|v2_hot|dex={venue.get('dex_name')}|product_risk={risk}|product_policy={product_policy.get('reason')}|wallet_specific_simulation_required"})
    return rows,rej

def _scan_v3_chain(app,ctx,settings,checks_budget:int,routes_budget:int)->tuple[list[dict],list[dict]]:
    rows=[];rej=[];now=int(time.time());probe=_scanner_input_base(app,ctx.config.chain_id,settings);max_impact=max(1,min(5000,_int(settings.get('max_price_impact_bps','200'),200)));min_edge=max(Decimal(0),_dec(settings.get('direct_market_min_edge_base','0'),'0'))
    try:trader=LiveTrader(app,ctx.config.slug,require_wallet=False)
    except Exception as exc:return rows,[{'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'','stage':'rpc','reason':f'{type(exc).__name__}:{exc}'}]
    checked=0
    for venue in _venues(app,ctx.config.chain_id,'V3'):
        if not venue.get('quoter'):continue
        pools=_v3_graph_rows(app,ctx.config.chain_id,venue['factory'])
        dynamic=allowed_product_addresses(app.csv_dir,ctx.config.chain_id,include_shadow=True,max_tokens=max(4,_int(settings.get('product_max_scan_tokens_per_chain','60'),60)))
        if dynamic:
            allowed={x.lower() for x in dynamic}|{str(trader.chain.wrapped_base_address).lower()}
            pools=[r for r in pools if str(r.get('token0') or '').lower() in allowed and str(r.get('token1') or '').lower() in allowed]
        paths=_v3_triangles(pools,trader.chain.wrapped_base_address,max(1,checks_budget-checked))
        if not paths:
            rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'','stage':'graph','reason':f"{venue.get('dex_name')}:no_complete_v3_triangle_in_seed_registry"})
        for path,fees in paths:
            if checked>=checks_budget or len(rows)>=routes_budget:break
            checked+=1
            product_policy=route_product_policy(app.csv_dir,ctx.config.chain_id,path)
            risk=int(product_policy.get('risk_level') or 9)
            risk_impact=max_impact
            if risk==2:risk_impact=min(risk_impact,max(1,_int(settings.get('product_level2_max_price_impact_bps','150'),150)))
            elif risk>=3:risk_impact=min(risk_impact,max(1,_int(settings.get('product_level3_max_price_impact_bps','75'),75)))
            try:q=_v3_quote(trader,venue['quoter'],path,fees,probe)
            except Exception as exc:
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'>'.join(path),'stage':'quote','reason':f"{venue.get('dex_name')}:{type(exc).__name__}:{exc}"});continue
            if q['gross_profit']<=0:
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'>'.join(path),'stage':'edge','reason':f"{venue.get('dex_name')}:non_positive_gross_edge:{q['gross_profit']}"});continue
            try:
                q2=_v3_quote(trader,venue['quoter'],path,fees,probe*2);ratio=(q2['amount_out']/Decimal(2))/q['amount_out'] if q['amount_out']>0 else Decimal(0);impact=max(Decimal(0),(Decimal(1)-ratio)*Decimal(10000))
            except Exception as exc:
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'>'.join(path),'stage':'liquidity','reason':f"2x_quote_failed:{type(exc).__name__}"});continue
            if impact>Decimal(risk_impact):
                rej.append({'observed_at_epoch':now,'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'route_kind':'V3_CYCLE','route_path':'>'.join(path),'stage':'product_risk','reason':f"risk_level={risk}:price_impact_bps:{impact:.2f}>{risk_impact}"});continue
            slip=max(Decimal(0),q['gross_profit'])*Decimal(trader._slippage_bps())/Decimal(10000);conservative=q['gross_profit']-slip
            if conservative<=min_edge:continue
            text='>'.join(path);fee_text='>'.join(str(x) for x in fees);rid=hashlib.sha256(f"V3|{ctx.config.chain_id}|{venue['router']}|{text}|{fee_text}".encode()).hexdigest()[:20]
            rows.append({'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'wallet':'DIRECT_MARKET','behaviour':f"DIRECT_{str(venue.get('dex_name') or 'V3').upper().replace(' ','_')}_V3_TRIANGULAR_ARBITRAGE",'route_id':rid,'route_path':text,'route_kind':'V3_CYCLE','protocol':'PANCAKE_V3','router_address':venue['router'],'quoter_address':venue['quoter'],'route_fees':fee_text,'venue_plan':venue.get('dex_name') or 'V3','execution_mode':'LIVE_ATOMIC_SINGLE_ROUTER','scanner_exact':'true','observed_at_epoch':now,'quote_input_base':f'{probe:f}','source_input_base':f'{probe:f}','quoted_output_base':f"{q['amount_out']:f}",'expected_gross_profit_base':f"{q['gross_profit']:f}",'estimated_gas_base':'0','gas_estimate_units':q.get('quoter_gas',0),'builder_fee_base':'0','slippage_reserve_base':f'{slip:f}','price_impact_bps':f'{impact:.2f}','copy_score':'','source_verified':'true','exact_quote_ok':'true','simulation_ok':'false','liquidity_ok':'true','sellability_ok':'true','route_approved':'true','whole_route_approved':'true','atomic_profit_protection':'true','canary_complete':'false','enabled':'true' if venue.get('auto_execute',False) and product_policy.get('auto_trade') else 'false','notes':f"full_power|v3|dex={venue.get('dex_name')}|product_risk={risk}|product_policy={product_policy.get('reason')}|wallet_specific_simulation_required"})
    return rows,rej


def _scan_cross_v2_chain(app,ctx,settings,checks_budget:int)->tuple[list[dict],list[dict]]:
    """Find two-router V2 round trips, but fail closed for live signing until an atomic executor is configured."""
    rows=[];rej=[];now=int(time.time());venues=_venues(app,ctx.config.chain_id,'V2')
    if not _bool(settings.get('cross_dex_scanner_enabled','true'),True) or len(venues)<2:return rows,rej
    try:trader=LiveTrader(app,ctx.config.slug,require_wallet=False)
    except Exception:return rows,rej
    probe=_scanner_input_base(app,ctx.config.chain_id,settings);raw=int(probe*Decimal(10**18));wrapped=Web3.to_checksum_address(ctx.config.wrapped_base_address)
    tokens=allowed_product_addresses(app.csv_dir,ctx.config.chain_id,include_shadow=True,max_tokens=max(4,_int(settings.get('product_max_scan_tokens_per_chain','60'),60)))
    if not tokens:tokens=_configured_token_seeds(app.csv_dir,ctx.config.chain_id)
    tokens=[x for x in tokens if x.lower()!=wrapped.lower()];checks=0
    for va,vb in product(venues,venues):
        if va['router'].lower()==vb['router'].lower():continue
        for token in tokens:
            if checks>=checks_budget:return rows,rej
            checks+=1;token=Web3.to_checksum_address(token)
            try:
                ra=trader.w3.eth.contract(address=va['router'],abi=V2_ROUTER_QUOTE_ABI);rb=trader.w3.eth.contract(address=vb['router'],abi=V2_ROUTER_QUOTE_ABI)
                mid=int(ra.functions.getAmountsOut(raw,[wrapped,token]).call()[-1]);out=int(rb.functions.getAmountsOut(mid,[token,wrapped]).call()[-1])
            except Exception:continue
            gross=Decimal(out-raw)/Decimal(10**18)
            if gross<=0:continue
            text=f'{wrapped}>{token}>{wrapped}';rid=hashlib.sha256(f"XDEX|{ctx.config.chain_id}|{va['router']}|{vb['router']}|{token}".encode()).hexdigest()[:20]
            rows.append({'chain_id':ctx.config.chain_id,'chain_slug':ctx.config.slug,'wallet':'DIRECT_MARKET','behaviour':'CROSS_DEX_V2_ARBITRAGE_CANDIDATE','route_id':rid,'route_path':text,'route_kind':'CROSS_DEX_V2','protocol':'MULTI_V2','router_address':va['router'],'quoter_address':'','route_fees':'','venue_plan':f"{va.get('dex_name')}:{va['router']};{vb.get('dex_name')}:{vb['router']}",'execution_mode':'SHADOW_ATOMIC_EXECUTOR_REQUIRED','scanner_exact':'true','observed_at_epoch':now,'quote_input_base':f'{probe:f}','source_input_base':f'{probe:f}','quoted_output_base':f'{Decimal(out)/Decimal(10**18):f}','expected_gross_profit_base':f'{gross:f}','estimated_gas_base':'0','gas_estimate_units':0,'builder_fee_base':'0','slippage_reserve_base':'0','price_impact_bps':'','copy_score':'','source_verified':'true','exact_quote_ok':'true','simulation_ok':'false','liquidity_ok':'true','sellability_ok':'true','route_approved':'true','whole_route_approved':'true','atomic_profit_protection':'false','canary_complete':'false','enabled':'false','notes':'full_power|cross_dex_detected|NO_SEQUENTIAL_EOA_EXECUTION|deploy_atomic_executor_to_enable'})
    return rows,rej


def scan_full_power_hot_routes(app,contexts)->tuple[Path,list[dict],list[dict]]:
    settings=load_kv_scoped(Path(app.csv_dir)/'auto_trading_settings.csv',0);out=Path(app.csv_dir)/'auto'/'full_power_opportunities.csv';rej_path=Path(app.csv_dir)/'auto'/'full_power_rejections.csv'
    if not _bool(settings.get('full_power_enabled','true'),True):return out,[],[]
    max_checks=max(10,min(500,_int(settings.get('fast_market_max_candidate_checks','60'),60)));max_routes=max(1,min(100,_int(settings.get('fast_market_max_routes_per_pass','20'),20)));ctxs=list(contexts);per=max(5,max_checks//max(1,len(ctxs)))
    rows=[];rejected=[]
    def scan_one(ctx):
        v2_budget=max(2,int(per*0.35));v3_budget=max(2,int(per*0.50));cross_budget=max(1,per-v2_budget-v3_budget);route_budget=max(1,max_routes//max(1,len(ctxs)));r0,e0=_scan_v2_hot_chain(app,ctx,settings,v2_budget,route_budget);r1,e1=_scan_v3_chain(app,ctx,settings,v3_budget,route_budget);r2,e2=_scan_cross_v2_chain(app,ctx,settings,cross_budget);return r0+r1+r2,e0+e1+e2
    workers=max(1,min(len(ctxs),_int(settings.get('full_power_parallel_chains','5'),5)))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='power-chain') as ex:
        futs=[ex.submit(scan_one,c) for c in ctxs]
        for f in as_completed(futs):
            try:r,e=f.result();rows.extend(r);rejected.extend(e)
            except Exception as exc:rejected.append({'observed_at_epoch':int(time.time()),'chain_id':'','chain_slug':'','route_kind':'FULL_POWER','route_path':'','stage':'thread','reason':f'{type(exc).__name__}:{exc}'})
    # Keep current V2 graph-first opportunities too; fast_market merges them separately.
    rows.sort(key=lambda r:_dec(r.get('expected_gross_profit_base'),'0')-_dec(r.get('slippage_reserve_base'),'0'),reverse=True);rows=rows[:max_routes]
    _atomic_write(out,rows,LIVE_HEADERS);_atomic_rows(rej_path,rejected[-1500:],POWER_REJECT_HEADERS)
    return out,rows,rejected
