from __future__ import annotations

import csv
import hashlib
import os
import threading
import time
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations
from pathlib import Path

from web3 import Web3

from .config import load_dex_registry, load_kv_scoped
from .live_executor import LiveTrader
from .route_scanner import LIVE_HEADERS, FACTORY_ABI, _atomic_write, _bool, _dec, _int, _route_tokens, _scanner_input_base


FACTORY_ENUM_ABI = FACTORY_ABI + [
    {"type":"function","name":"allPairsLength","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"allPairs","stateMutability":"view","inputs":[{"name":"","type":"uint256"}],"outputs":[{"name":"pair","type":"address"}]},
]
PAIR_ABI = [
    {"type":"function","name":"token0","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"address"}]},
    {"type":"function","name":"token1","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"address"}]},
]
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
POOL_HEADERS = [
    "chain_id","chain_slug","dex_name","router_address","factory_address",
    "pair_address","token0","token1","first_seen_epoch","last_seen_epoch",
]
STATE_HEADERS = ["chain_id","chain_slug","factory_address","next_pair_index","all_pairs_length","updated_epoch"]
REJECT_HEADERS = ["observed_at_epoch","chain_id","chain_slug","route_path","stage","reason"]
LIVE_FEED_LOCK = threading.Lock()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _atomic_rows(path: Path, rows: list[dict], headers: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows([{h:r.get(h, "") for h in headers} for r in rows])
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _configured_token_seeds(csv_dir: Path, chain_id: int) -> list[str]:
    out=[]
    for r in _rows(Path(csv_dir)/"tokens.csv"):
        if str(r.get("chain_id") or "").strip()!=str(chain_id) or not _bool(r.get("enabled"),True):
            continue
        a=(r.get("address") or "").strip()
        if not Web3.is_address(a):
            continue
        a=Web3.to_checksum_address(a)
        if a not in out:
            out.append(a)
    return out


def _learned_token_counts(ctx, limit_rows: int=800) -> Counter:
    counts: Counter=Counter()
    try:
        rows=ctx.conn.execute(
            """SELECT route_fingerprint,proof_quality,net_base
               FROM profit_evidence
               WHERE route_fingerprint IS NOT NULL AND route_fingerprint!=''
               ORDER BY CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN 0 ELSE 1 END,
                        COALESCE(net_base,0) DESC, created_at DESC LIMIT ?""",
            (int(limit_rows),),
        ).fetchall()
    except Exception:
        return counts
    for r in rows:
        weight=4 if str(r["proof_quality"] or "")=="PROVEN_WRAPPED_BASE" else 1
        for a in _route_tokens(str(r["route_fingerprint"] or "")):
            counts[a]+=weight
    return counts


def _state_get(state_rows: list[dict], chain_id: int, factory: str) -> dict|None:
    for r in state_rows:
        if str(r.get("chain_id") or "")==str(chain_id) and (r.get("factory_address") or "").lower()==factory.lower():
            return r
    return None


def _upsert_pool(pool_rows: list[dict], trader: LiveTrader, dex_name: str, router: str,
                 factory: str, pair: str, token0: str, token1: str, now: int):
    key=(str(trader.chain.chain_id),factory.lower(),pair.lower())
    for r in pool_rows:
        if (str(r.get("chain_id") or ""),(r.get("factory_address") or "").lower(),(r.get("pair_address") or "").lower())==key:
            r.update({"dex_name":dex_name,"router_address":router,"last_seen_epoch":now})
            return
    pool_rows.append({
        "chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,"dex_name":dex_name,
        "router_address":router,"factory_address":factory,"pair_address":pair,
        "token0":token0,"token1":token1,"first_seen_epoch":now,"last_seen_epoch":now,
    })


def _crawl_factory_pairs(trader: LiveTrader, factory_address: str, app, settings: dict, now: int,
                         *, dex_name="V2", per_cycle_override: int|None=None):
    """Incrementally crawl a V2 factory and retain a local pair graph.

    Fast mode uses only a small tail/history slice; the full learning loop is no longer
    required before the live market scanner can run.
    """
    pool_path=Path(app.csv_dir)/"auto"/"pool_registry.csv"
    state_path=Path(app.csv_dir)/"auto"/"direct_market_state.csv"
    pool_rows=_rows(pool_path);state_rows=_rows(state_path)
    factory=trader.w3.eth.contract(address=Web3.to_checksum_address(factory_address),abi=FACTORY_ENUM_ABI)
    try: total=int(factory.functions.allPairsLength().call())
    except Exception: return pool_rows
    if total<=0: return pool_rows
    if per_cycle_override is None:
        per_cycle=max(0,min(500,_int(settings.get("direct_market_pairs_per_dex_cycle","60"),60)))
    else:
        per_cycle=max(0,min(100,int(per_cycle_override)))
    if per_cycle<=0: return pool_rows
    tail=max(0,min(5000,_int(settings.get("direct_market_bootstrap_tail_pairs","240"),240)))
    st=_state_get(state_rows,trader.chain.chain_id,factory_address);new_state=st is None
    if st:
        try:start=int(st.get("next_pair_index") or 0)
        except Exception:start=0
        if start>=total:start=0
    else:
        start=max(0,total-min(total,tail));st={"chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,
            "factory_address":factory_address,"next_pair_index":start,"all_pairs_length":total,"updated_epoch":now}
        state_rows.append(st)
    recent_watch=max(0,min(100,_int(settings.get("direct_market_recent_pairs_watch","10"),10)))
    if per_cycle_override is not None:
        recent_watch=min(recent_watch,max(1,per_cycle//2))
    history_budget=per_cycle if new_state else max(1,per_cycle-min(recent_watch,max(0,per_cycle-1)))
    end=min(total,start+history_budget);indices=list(range(start,end))
    if not new_state and recent_watch:
        indices += list(range(max(0,total-recent_watch),total))
    indices=list(dict.fromkeys(indices))[:per_cycle]
    for i in indices:
        try:
            pair=Web3.to_checksum_address(factory.functions.allPairs(i).call())
            if pair.lower()==ZERO_ADDRESS.lower():continue
            pc=trader.w3.eth.contract(address=pair,abi=PAIR_ABI)
            t0=Web3.to_checksum_address(pc.functions.token0().call());t1=Web3.to_checksum_address(pc.functions.token1().call())
        except Exception:continue
        _upsert_pool(pool_rows,trader,dex_name,trader.router_address,factory_address,pair,t0,t1,now)
    next_i=end if end<total else 0
    st.update({"next_pair_index":next_i,"all_pairs_length":total,"updated_epoch":now})
    _atomic_rows(pool_path,pool_rows[-30000:],POOL_HEADERS);_atomic_rows(state_path,state_rows,STATE_HEADERS)
    return pool_rows


def _seed_factory_pairs(trader: LiveTrader, factory_address: str, dex_name: str, app, settings: dict,
                        pool_rows: list[dict], now: int):
    """Populate the graph immediately from operator-approved liquid token seeds.

    Only canonical factory.getPair() results are accepted. This avoids waiting for a
    multi-million-pair factory crawl before established liquid pools become searchable.
    """
    seeds=_configured_token_seeds(app.csv_dir,trader.chain.chain_id)
    wrapped=Web3.to_checksum_address(trader.chain.wrapped_base_address)
    ordered=[wrapped]+[a for a in seeds if a.lower()!=wrapped.lower()]
    max_seeds=max(2,min(12,_int(settings.get("direct_market_max_seed_tokens_per_chain","8"),8)))
    ordered=ordered[:max_seeds]
    budget=max(0,min(100,_int(settings.get("direct_market_seed_pair_checks_per_venue","28"),28)))
    factory=trader.w3.eth.contract(address=Web3.to_checksum_address(factory_address),abi=FACTORY_ABI)
    checks=0
    for a,b in combinations(ordered,2):
        if checks>=budget:break
        checks+=1
        try:
            pair=Web3.to_checksum_address(factory.functions.getPair(a,b).call())
            if pair.lower()==ZERO_ADDRESS.lower() or not trader.w3.eth.get_code(pair):continue
            _upsert_pool(pool_rows,trader,dex_name,trader.router_address,factory_address,pair,
                         Web3.to_checksum_address(a),Web3.to_checksum_address(b),now)
        except Exception:continue
    _atomic_rows(Path(app.csv_dir)/"auto"/"pool_registry.csv",pool_rows[-30000:],POOL_HEADERS)
    return pool_rows


def _token_universe(ctx, app, trader: LiveTrader, pool_rows: list[dict], settings: dict, *, include_learned=True) -> list[str]:
    wrapped=Web3.to_checksum_address(trader.chain.wrapped_base_address)
    max_tokens=max(4,min(120,_int(settings.get("direct_market_max_tokens_per_chain","32"),32)))
    out=[wrapped]
    for a in _configured_token_seeds(app.csv_dir,trader.chain.chain_id):
        if a not in out:out.append(a)
    # Prefer tokens actually connected to wrapped base in the local current graph.
    for r in sorted(pool_rows,key=lambda x:int(float(x.get("last_seen_epoch") or 0)),reverse=True):
        if str(r.get("chain_id") or "")!=str(trader.chain.chain_id):continue
        if (r.get("factory_address") or "").lower() not in {x.get("factory","").lower() for x in load_dex_registry(app.csv_dir,trader.chain.chain_id)}:continue
        a=(r.get("token0") or "").strip();b=(r.get("token1") or "").strip()
        x=None
        if a.lower()==wrapped.lower() and Web3.is_address(b):x=Web3.to_checksum_address(b)
        elif b.lower()==wrapped.lower() and Web3.is_address(a):x=Web3.to_checksum_address(a)
        if x and x not in out:out.append(x)
        if len(out)>=max_tokens:break
    if include_learned and len(out)<max_tokens:
        for a,_ in _learned_token_counts(ctx,max(200,max_tokens*50)).most_common(max_tokens*4):
            if a not in out:out.append(a)
            if len(out)>=max_tokens:break
    return out[:max_tokens]


def _pool_graph(pool_rows: list[dict], chain_id: int, factory_address: str):
    graph=defaultdict(set);pairs={}
    for r in pool_rows:
        if str(r.get("chain_id") or "")!=str(chain_id):continue
        if (r.get("factory_address") or "").lower()!=factory_address.lower():continue
        a=(r.get("token0") or "").strip();b=(r.get("token1") or "").strip();p=(r.get("pair_address") or "").strip()
        if not (Web3.is_address(a) and Web3.is_address(b) and Web3.is_address(p)):continue
        aa=Web3.to_checksum_address(a);bb=Web3.to_checksum_address(b)
        graph[aa.lower()].add(bb);graph[bb.lower()].add(aa)
        pairs[tuple(sorted((aa.lower(),bb.lower())))]=p
    return graph,pairs


def _graph_triangles(pool_rows: list[dict], chain_id: int, factory_address: str, wrapped: str,
                     token_universe: list[str], max_checks: int):
    """Return only triangles whose three edges already exist in the actual pair graph."""
    graph,_=_pool_graph(pool_rows,chain_id,factory_address);w=Web3.to_checksum_address(wrapped);wl=w.lower()
    allowed={x.lower() for x in token_universe};neighbors=[x for x in graph.get(wl,set()) if x.lower() in allowed]
    # Seed/config order is a liquidity hint; recent registry neighbors fill the rest.
    rank={a.lower():i for i,a in enumerate(token_universe)};neighbors.sort(key=lambda a:rank.get(a.lower(),999999))
    out=[];seen=set()
    for a in neighbors:
        for b in graph.get(a.lower(),set()):
            if b.lower()==wl or b.lower()==a.lower() or b.lower() not in allowed:continue
            if not any(x.lower()==b.lower() for x in graph.get(wl,set())):continue
            for path in ([w,a,b,w],[w,b,a,w]):
                key=tuple(x.lower() for x in path)
                if key not in seen:
                    seen.add(key);out.append(path)
                    if len(out)>=max_checks:return out
    return out


def _reject(now,cid,slug,path,stage,reason):
    return {"observed_at_epoch":now,"chain_id":cid,"chain_slug":slug,"route_path":">".join(path),"stage":stage,"reason":str(reason)[:500]}


def _v2_venues(app, chain_id: int) -> list[dict]:
    out=[]
    for r in load_dex_registry(app.csv_dir,chain_id):
        if (r.get("version") or "").strip().upper()!="V2":continue
        router=(r.get("router") or "").strip();factory=(r.get("factory") or "").strip()
        if not (Web3.is_address(router) and Web3.is_address(factory)):continue
        out.append({"dex_name":r.get("dex_name") or "V2","router":Web3.to_checksum_address(router),"factory":Web3.to_checksum_address(factory)})
    return out


def scan_direct_market_routes(app, contexts, *, fast: bool=False) -> tuple[Path,list[dict]]:
    """Graph-first current V2 triangular scanner.

    v2.2 never guesses A/B edges. It constructs candidates from pairs that the local
    factory registry already proves exist. Liquid seed pairs are queried directly via
    factory.getPair(), so established pools are available immediately. In fast mode the
    scan is independent of the long wallet-learning cycle and uses a small RPC budget.
    """
    settings=load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",0)
    out_path=Path(app.csv_dir)/"auto"/"direct_market_opportunities.csv";reject_path=Path(app.csv_dir)/"auto"/"direct_market_rejections.csv"
    if not _bool(settings.get("direct_market_scanner_enabled","true"),True):
        _atomic_write(out_path,[],LIVE_HEADERS);_atomic_rows(reject_path,[],REJECT_HEADERS);return out_path,[]
    if fast and not _bool(settings.get("fast_market_enabled","true"),True):
        return out_path,_rows(out_path)

    if fast:
        max_routes=max(1,min(100,_int(settings.get("fast_market_max_routes_per_pass","20"),20)))
        max_checks=max(max_routes,min(2000,_int(settings.get("fast_market_max_candidate_checks","120"),120)))
        pair_crawl=max(0,min(50,_int(settings.get("fast_market_pairs_per_dex_pass","6"),6)))
    else:
        max_routes=max(1,min(200,_int(settings.get("direct_market_max_routes_per_cycle","40"),40)))
        max_checks=max(max_routes,min(10000,_int(settings.get("direct_market_max_candidate_checks","600"),600)))
        pair_crawl=None
    max_impact=max(1,min(5000,_int(settings.get("max_price_impact_bps","200"),200)))
    min_edge=max(Decimal(0),_dec(settings.get("direct_market_min_edge_base","0"),"0"))
    now=int(time.time());rows=[];rejected=[]
    enabled=[ctx for ctx in contexts if getattr(ctx.config,"enabled",True)];chain_count=max(1,len(enabled))
    checks_per_chain=max(1,(max_checks+chain_count-1)//chain_count);routes_per_chain=max(1,(max_routes+chain_count-1)//chain_count)

    for ctx in enabled:
        chain_checks=0;chain_routes=0
        venues=_v2_venues(app,ctx.config.chain_id)
        if not venues:
            rejected.append(_reject(now,ctx.config.chain_id,ctx.config.slug,[],"chain","no_enabled_v2_venue_in_dex_registry"));continue
        venue_budget=max(1,checks_per_chain//max(1,len(venues)))
        venue_route_budget=max(1,routes_per_chain//max(1,len(venues)))
        for venue in venues:
            if chain_checks>=checks_per_chain or chain_routes>=routes_per_chain:break
            try:
                trader=LiveTrader(app,ctx.config.slug,require_wallet=False,router_override=venue["router"])
                if not trader.w3.eth.get_code(venue["factory"]):raise RuntimeError("factory_has_no_code")
            except Exception as exc:
                rejected.append(_reject(now,ctx.config.chain_id,ctx.config.slug,[],"venue",f"{venue['dex_name']}:{type(exc).__name__}:{exc}"));continue
            pool_rows=_crawl_factory_pairs(trader,venue["factory"],app,settings,now,dex_name=venue["dex_name"],per_cycle_override=pair_crawl)
            pool_rows=_seed_factory_pairs(trader,venue["factory"],venue["dex_name"],app,settings,pool_rows,now)
            universe=_token_universe(ctx,app,trader,pool_rows,settings,include_learned=not fast)
            probe=_scanner_input_base(app,trader.chain.chain_id,settings);slippage_bps=trader._slippage_bps()
            paths=_graph_triangles(pool_rows,trader.chain.chain_id,venue["factory"],trader.chain.wrapped_base_address,universe,venue_budget)
            if not paths:
                rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,[],"graph",f"{venue['dex_name']}:no_complete_wrapped_base_triangle_in_current_registry"))
            for path in paths:
                if chain_checks>=checks_per_chain or chain_routes>=routes_per_chain:break
                chain_checks+=1
                try:q=trader.cycle_quote(path,probe);gross=q["gross_profit"];out=q["amount_out"]
                except Exception as exc:
                    rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,path,"quote",f"{venue['dex_name']}:{type(exc).__name__}:{exc}"));continue
                if gross<=0:
                    if len(rejected)<1500:rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,path,"edge",f"{venue['dex_name']}:non_positive_gross_edge:{gross}"))
                    continue
                try:
                    q2=trader.cycle_quote(path,probe*2);ratio=(q2["amount_out"]/Decimal(2))/out if out>0 else Decimal(0)
                    impact=max(Decimal(0),(Decimal(1)-ratio)*Decimal(10000))
                except Exception as exc:
                    rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,path,"liquidity",f"{venue['dex_name']}:2x_quote_failed:{type(exc).__name__}"));continue
                if impact>Decimal(max_impact):
                    rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,path,"liquidity",f"{venue['dex_name']}:price_impact_bps:{impact:.2f}>{max_impact}"));continue
                slip=max(Decimal(0),gross)*Decimal(slippage_bps)/Decimal(10000);conservative=gross-slip
                if conservative<=min_edge:
                    rejected.append(_reject(now,trader.chain.chain_id,trader.chain.slug,path,"edge",f"{venue['dex_name']}:edge_after_slippage:{conservative}"));continue
                text=">".join(path);rid=hashlib.sha256(f"DIRECT|{trader.chain.chain_id}|{trader.router_address}|{text}".encode()).hexdigest()[:20]
                rows.append({
                    "chain_id":trader.chain.chain_id,"chain_slug":trader.chain.slug,"wallet":"DIRECT_MARKET",
                    "behaviour":f"DIRECT_MARKET_{venue['dex_name'].upper().replace(' ','_')}_V2_TRIANGULAR_ARBITRAGE",
                    "route_id":rid,"route_path":text,"router_address":trader.router_address,"scanner_exact":"true",
                    "observed_at_epoch":now,"quote_input_base":f"{probe:f}","source_input_base":f"{probe:f}","quoted_output_base":f"{out:f}",
                    "expected_gross_profit_base":f"{gross:f}","estimated_gas_base":"0","gas_estimate_units":0,"builder_fee_base":"0",
                    "slippage_reserve_base":f"{slip:f}","price_impact_bps":f"{impact:.2f}","copy_score":"","source_verified":"true",
                    "exact_quote_ok":"true","simulation_ok":"false","liquidity_ok":"true","sellability_ok":"false","route_approved":"true",
                    "whole_route_approved":"true","atomic_profit_protection":"false","canary_complete":"false","enabled":"true",
                    "notes":f"direct_market|graph_first|dex={venue['dex_name']}|factory_pairs_validated|wallet_specific_simulation_required",
                });chain_routes+=1
    rows.sort(key=lambda r:_dec(r.get("expected_gross_profit_base"),"0")-_dec(r.get("slippage_reserve_base"),"0"),reverse=True)
    rows=rows[:max_routes];rejected=rejected[-1500:]
    _atomic_write(out_path,rows,LIVE_HEADERS);_atomic_rows(reject_path,rejected,REJECT_HEADERS)
    return out_path,rows


def merge_live_opportunities(app,*groups:list[dict]) -> tuple[Path,list[dict]]:
    best={}
    for group in groups:
        for r in group or []:
            key=(str(r.get("chain_id") or ""),(r.get("router_address") or "").lower(),(r.get("route_path") or "").lower(),str(r.get("route_kind") or "V2_CYCLE").upper(),str(r.get("route_fees") or ""))
            score=_dec(r.get("expected_gross_profit_base"),"0")-_dec(r.get("slippage_reserve_base"),"0")-_dec(r.get("estimated_gas_base"),"0")
            old=best.get(key)
            if old is None or score>old[0]:best[key]=(score,r)
    rows=[v[1] for v in sorted(best.values(),key=lambda x:x[0],reverse=True)]
    out=Path(app.csv_dir)/"live_opportunities.csv"
    with LIVE_FEED_LOCK:_atomic_write(out,rows,LIVE_HEADERS)
    return out,rows
