from __future__ import annotations
import csv, hashlib, time
from pathlib import Path
from .config import load_kv_scoped

COPY_ELIGIBLE_DEFAULT={
    "TRIANGULAR_MULTI_HOP_ARBITRAGE",
    "TWO_ASSET_ARBITRAGE",
    "STABLECOIN_ARBITRAGE",
    "PRIVATE_ROUTED_ARBITRAGE",
}
def _bool(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}
def _float(v,default=0.0):
    try:return float(v)
    except:return float(default)
def _int(v,default=0):
    try:return int(float(v))
    except:return int(default)
def _rows(path):
    p=Path(path)
    if not p.exists():return []
    with p.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def _settings(settings):return load_kv_scoped(settings.csv_dir/"copy_settings.csv",settings.chain_id)
def _allowed(cfg):
    raw=cfg.get("allowed_behaviours","|".join(sorted(COPY_ELIGIBLE_DEFAULT)))
    return {x.strip().upper() for x in raw.split("|") if x.strip()}
def _metric_score(value,target):
    value=max(0.0,float(value or 0));target=max(1e-12,float(target or 0))
    return max(0.0,min(100.0,(value/target)*100.0))

def refresh_copy_candidates(conn,settings):
    cfg=_settings(settings)
    if not _bool(cfg.get("enabled","true"),True):
        conn.execute("DELETE FROM copy_wallet_candidates");conn.commit()
        return {"evaluated":0,"passed":0,"disabled":True}
    min_bot=_float(cfg.get("min_bot_score","60"),60)
    min_conf=_float(cfg.get("min_behaviour_confidence","75"),75)
    min_proven=_int(cfg.get("min_proven_trades","20"),20)
    min_ratio=_float(cfg.get("min_positive_ratio","0.70"),0.70)
    min_profit=_float(cfg.get("min_total_net_base","0.01"),0.01)
    min_pph=_float(cfg.get("min_profit_per_hour_base","0.001"),0.001)
    min_hours=_float(cfg.get("min_active_hours","1"),1)
    max_negative_ratio=_float(cfg.get("max_negative_ratio","0.30"),0.30)
    max_loss_to_profit=_float(cfg.get("max_single_loss_to_total_profit_ratio","0.50"),0.50)
    min_copy_score=_float(cfg.get("min_copy_score","65"),65)
    allowed=_allowed(cfg)
    target_profit=_float(cfg.get("score_target_total_profit_base","0.50"),0.50)
    target_pph=_float(cfg.get("score_target_profit_per_hour_base","0.05"),0.05)
    target_proven=max(1,_int(cfg.get("score_target_proven_trades","100"),100))
    now=int(time.time())
    conn.execute("DELETE FROM copy_wallet_candidates")
    rows=conn.execute("""SELECT w.wallet,w.behaviour,w.evidence_count,w.proven_count,w.positive_count,
        w.negative_count,w.total_net_base,w.avg_net_base,w.active_hours,w.profit_per_hour_base,
        w.median_seconds_between_positive,COALESCE(s.bot_score,0) bot_score
        FROM wallet_behaviour_rankings w LEFT JOIN wallet_scores s ON s.wallet=w.wallet
        WHERE w.evidence_count>0""").fetchall()
    passed=0
    for r in rows:
        stats=conn.execute("""SELECT AVG(behaviour_confidence) avg_conf,
            MAX(CASE WHEN profit_base>0 THEN profit_base END) max_pos,
            MIN(CASE WHEN profit_base<0 THEN profit_base END) min_neg
            FROM trade_behaviour_evidence WHERE wallet=? AND behaviour=?""",
            (r["wallet"],r["behaviour"])).fetchone()
        bot=float(r["bot_score"] or 0);conf=float(stats["avg_conf"] or 0)
        proven=int(r["proven_count"] or 0);pos=int(r["positive_count"] or 0);neg=int(r["negative_count"] or 0)
        ratio=float(pos/proven) if proven else 0.0
        total=float(r["total_net_base"] or 0);pph=float(r["profit_per_hour_base"] or 0)
        hours=float(r["active_hours"] or 0);max_pos=float(stats["max_pos"] or 0);max_loss=abs(float(stats["min_neg"] or 0))
        negative_ratio=float(neg/proven) if proven else 1.0
        behaviour=(r["behaviour"] or "").upper();reasons=[]
        if behaviour not in allowed:reasons.append("behaviour_not_copy_eligible")
        if bot<min_bot:reasons.append("bot_score_below_minimum")
        if conf<min_conf:reasons.append("behaviour_confidence_below_minimum")
        if proven<min_proven:reasons.append("not_enough_proven_closed_cycles")
        if ratio<min_ratio:reasons.append("positive_ratio_below_minimum")
        if total<min_profit:reasons.append("historical_net_profit_below_minimum")
        if pph<min_pph:reasons.append("historical_profit_speed_below_minimum")
        if hours<min_hours:reasons.append("observation_window_too_short")
        if negative_ratio>max_negative_ratio:reasons.append("negative_trade_ratio_too_high")
        if total>0 and max_loss/total>max_loss_to_profit:reasons.append("single_loss_too_large_vs_total_profit")
        profit_score=_metric_score(total,target_profit);speed_score=_metric_score(pph,target_pph)
        consistency=max(0.0,min(100.0,ratio*100.0));evidence=max(0.0,min(100.0,(proven/target_proven)*100.0))
        copy_score=profit_score*.25+speed_score*.20+consistency*.20+evidence*.15+max(0,min(100,conf))*.10+max(0,min(100,bot))*.10
        if copy_score<min_copy_score:reasons.append("copy_score_below_minimum")
        status="PASS" if not reasons else "REJECT"
        if status=="PASS":passed+=1
        conn.execute("""INSERT INTO copy_wallet_candidates(
          wallet,behaviour,status,pass_checks,copy_score,bot_score,avg_behaviour_confidence,evidence_count,
          proven_count,positive_count,negative_count,positive_ratio,total_net_base,profit_per_hour_base,
          active_hours,avg_net_base,max_positive_base,max_loss_base,median_seconds_between_positive,
          rejection_reasons,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (r["wallet"],r["behaviour"],status,1 if status=="PASS" else 0,copy_score,bot,conf,
           int(r["evidence_count"] or 0),proven,pos,neg,ratio,total,pph,hours,r["avg_net_base"],
           max_pos,max_loss,r["median_seconds_between_positive"],"|".join(reasons),now))
    conn.commit()
    return {"evaluated":len(rows),"passed":passed,"disabled":False}

def global_top20(contexts,csv_dir):
    cfg=load_kv_scoped(Path(csv_dir)/"copy_settings.csv",contexts[0].config.chain_id) if contexts else {}
    limit=max(1,min(100,_int(cfg.get("top_wallets","20"),20)))
    all_rows=[]
    for c in contexts:
        for r in c.conn.execute("""SELECT * FROM copy_wallet_candidates WHERE status='PASS'
                                   ORDER BY copy_score DESC,total_net_base DESC""").fetchall():
            all_rows.append({"chain_id":c.config.chain_id,"chain_slug":c.config.slug,"chain_name":c.config.name,
                             "explorer_url":c.config.explorer_url,"native_symbol":c.config.native_symbol,
                             "wrapped_base_symbol":c.config.wrapped_base_symbol,**{k:r[k] for k in r.keys()}})
    all_rows.sort(key=lambda x:(float(x.get("copy_score") or 0),float(x.get("positive_ratio") or 0),
                                int(x.get("proven_count") or 0)),reverse=True)
    unique=[];seen=set()
    for row in all_rows:
        key=(row["chain_id"],str(row["wallet"]).lower())
        if key in seen:continue
        seen.add(key);row["global_rank"]=len(unique)+1;unique.append(row)
        if len(unique)>=limit:break
    return unique

def export_top20(contexts,csv_dir):
    path=Path(csv_dir)/"auto"/"top20_copy_wallets.csv";path.parent.mkdir(parents=True,exist_ok=True)
    rows=global_top20(contexts,csv_dir)
    headers=["global_rank","chain_id","chain_slug","chain_name","wallet","behaviour","status","copy_score",
             "bot_score","avg_behaviour_confidence","proven_count","positive_count","negative_count",
             "positive_ratio","total_net_base","profit_per_hour_base","active_hours","max_positive_base",
             "max_loss_base","median_seconds_between_positive","updated_at"]
    with path.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader()
        for row in rows:w.writerow({h:row.get(h,"") for h in headers})
    return path,rows

def _candidate_map(contexts,csv_dir):
    return {(int(r["chain_id"]),str(r["wallet"]).lower()):r for r in global_top20(contexts,csv_dir)}
def _recommendation_id(cid,wallet,route,observed):
    return hashlib.sha256(f"{cid}|{wallet.lower()}|{route}|{observed}".encode()).hexdigest()[:24]

def generate_recommendations(contexts,csv_dir):
    candidates=_candidate_map(contexts,csv_dir);by_chain={c.config.chain_id:c for c in contexts}
    output=[];now=time.time()
    for row in _rows(Path(csv_dir)/"live_opportunities.csv"):
        if not _bool(row.get("enabled","true"),True):continue
        try:cid=int(row.get("chain_id") or 0)
        except:continue
        c=by_chain.get(cid)
        if not c:continue
        wallet=(row.get("wallet") or "").strip().lower()
        if not wallet:continue
        cand=candidates.get((cid,wallet));behaviour=(row.get("behaviour") or (cand or {}).get("behaviour") or "UNKNOWN").strip().upper()
        route=(row.get("route_id") or "unlabelled").strip();observed=_int(row.get("observed_at_epoch"),0)
        age=max(0.0,now-observed) if observed else 1e18
        cfg=load_kv_scoped(Path(csv_dir)/"copy_settings.csv",cid)
        source_input=_float(row.get("source_input_base"),0);gross=_float(row.get("expected_gross_profit_base"),0)
        gas=_float(row.get("estimated_gas_base"),0);builder=_float(row.get("builder_fee_base"),0);slippage=_float(row.get("slippage_reserve_base"),0)
        capture_pct=_float(cfg.get("copy_edge_capture_pct","50"),50)/100.0
        size_pct=_float(cfg.get("copy_size_pct_of_source","25"),25)/100.0
        max_input=_float(cfg.get("max_copy_input_base","1"),1);canary=_float(cfg.get("canary_input_base","0.05"),0.05)
        canary_required=_bool(cfg.get("canary_required","true"),True);canary_complete=_bool(row.get("canary_complete","false"),False)
        scanner_exact=_bool(row.get("scanner_exact"),False)
        if scanner_exact:
            # A scanner_exact row is already a fresh quote for our own proposed input,
            # not an historical source trade. Do not apply the copy-size/edge haircut again.
            recommended=_float(row.get("quote_input_base"),source_input)
            captured=max(0.0,gross)
        else:
            recommended=min(source_input*size_pct,max_input) if source_input>0 else 0.0
            if canary_required and not canary_complete and recommended>0:recommended=min(recommended,canary)
            captured=max(0.0,gross*capture_pct)
        conservative=captured-gas-builder-slippage
        min_profit=_float(cfg.get("min_conservative_profit_base","0.001"),0.001);max_age=_float(cfg.get("max_signal_age_seconds","2"),2)
        checks=[]
        def add(name,ok):checks.append((name,bool(ok)))
        add("wallet_in_approved_top20",cand is not None)
        add("signal_fresh",age<=max_age)
        add("source_verified",_bool(row.get("source_verified"),False) if _bool(cfg.get("require_source_verified","true"),True) else True)
        add("exact_quote",_bool(row.get("exact_quote_ok"),False) if _bool(cfg.get("require_exact_quote","true"),True) else True)
        add("simulation",_bool(row.get("simulation_ok"),False) if _bool(cfg.get("require_simulation","true"),True) else True)
        add("liquidity",_bool(row.get("liquidity_ok"),False) if _bool(cfg.get("require_liquidity_ok","true"),True) else True)
        add("sellability",_bool(row.get("sellability_ok"),False) if _bool(cfg.get("require_sellability_ok","true"),True) else True)
        add("route_approved",_bool(row.get("route_approved"),False) if _bool(cfg.get("require_route_approved","true"),True) else True)
        add("whole_route_approved",_bool(row.get("whole_route_approved"),False) if _bool(cfg.get("require_whole_route_approved","true"),True) else True)
        add("atomic_profit_protection",_bool(row.get("atomic_profit_protection"),False) if _bool(cfg.get("require_atomic_profit_protection","true"),True) else True)
        add("input_cap",recommended>0 and recommended<=max_input)
        add("minimum_conservative_profit",conservative>=min_profit)
        failed=[n for n,ok in checks if not ok];passed=len(checks)-len(failed)
        mode=(cfg.get("recommendation_mode","SHADOW") or "SHADOW").strip().upper()
        if cand is None:action="SKIP";reason="Wallet is not in the approved Top-20 copy universe."
        elif failed:action="OUT";reason="Failed: "+", ".join(failed)
        else:action="IN";reason=(f"All {len(checks)} configured gates passed on a fresh exact scanner quote." if scanner_exact else f"All {len(checks)} configured gates passed; follower gross edge is haircutted to {capture_pct*100:.0f}% before costs.")
        rec={"recommendation_id":_recommendation_id(cid,wallet,route,observed),"chain_id":cid,
             "chain_slug":c.config.slug,"chain_name":c.config.name,"wallet":wallet,"behaviour":behaviour,
             "route_id":route,"action":action,"recommendation_mode":mode,"reason":reason,
             "source_input_base":source_input,"recommended_input_base":recommended,
             "expected_gross_profit_base":gross,"captured_gross_profit_base":captured,
             "estimated_gas_base":gas,"builder_fee_base":builder,"slippage_reserve_base":slippage,
             "conservative_net_profit_base":conservative,"signal_age_seconds":age,"checks_passed":passed,
             "checks_failed":len(failed),"check_summary":";".join(f"{n}={'PASS' if ok else 'FAIL'}" for n,ok in checks),
             "observed_at":observed,"created_at":int(now)}
        output.append(rec)
        c.conn.execute("""INSERT INTO copy_trade_recommendations(
          recommendation_id,wallet,behaviour,route_id,action,recommendation_mode,reason,source_input_base,
          recommended_input_base,expected_gross_profit_base,captured_gross_profit_base,estimated_gas_base,
          builder_fee_base,slippage_reserve_base,conservative_net_profit_base,signal_age_seconds,checks_passed,
          checks_failed,check_summary,observed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(recommendation_id) DO UPDATE SET action=excluded.action,recommendation_mode=excluded.recommendation_mode,
          reason=excluded.reason,recommended_input_base=excluded.recommended_input_base,
          captured_gross_profit_base=excluded.captured_gross_profit_base,
          conservative_net_profit_base=excluded.conservative_net_profit_base,signal_age_seconds=excluded.signal_age_seconds,
          checks_passed=excluded.checks_passed,checks_failed=excluded.checks_failed,check_summary=excluded.check_summary,
          created_at=excluded.created_at""",
          (rec["recommendation_id"],wallet,behaviour,route,action,mode,reason,source_input,recommended,gross,captured,
           gas,builder,slippage,conservative,age,passed,len(failed),rec["check_summary"],observed,int(now)))
        c.conn.commit()
    path=Path(csv_dir)/"auto"/"copy_trade_recommendations.csv";path.parent.mkdir(parents=True,exist_ok=True)
    headers=["recommendation_id","chain_id","chain_slug","chain_name","wallet","behaviour","route_id","action",
             "recommendation_mode","reason","source_input_base","recommended_input_base","expected_gross_profit_base",
             "captured_gross_profit_base","estimated_gas_base","builder_fee_base","slippage_reserve_base",
             "conservative_net_profit_base","signal_age_seconds","checks_passed","checks_failed","check_summary",
             "observed_at","created_at"]
    with path.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader()
        for r in output:w.writerow({h:r.get(h,"") for h in headers})
    return path,output
