#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,os,signal,sys,time,urllib.parse,urllib.request
from decimal import Decimal,InvalidOperation
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from learnerbot.config import AppSettings,load_kv_scoped
from learnerbot.telegram import send_to_chats

SUCCESS_STATUSES={"SUCCESS","SUCCESS_FEE_PENDING"}
PRICE_URL="https://api.coingecko.com/api/v3/simple/price?"+urllib.parse.urlencode({
    "ids":"ethereum,binancecoin,polygon-ecosystem-token,matic-network",
    "vs_currencies":"usd","include_last_updated_at":"true"})
STAGES=[
(0,"START",{"fast_market_interval_seconds":"5","fast_market_max_candidate_checks":"60","product_max_scan_tokens_per_chain":"60","product_new_token_shadow_seconds":"300","product_established_age_seconds":"900","product_established_min_pools":"2","product_strict_min_pools":"2","product_level2_max_price_impact_bps":"150","product_level3_max_price_impact_bps":"100","max_auto_trades_per_hour":"12","cooldown_seconds":"5"}),
(2700,"EXPAND-1",{"product_max_scan_tokens_per_chain":"80","product_new_token_shadow_seconds":"240","product_established_age_seconds":"600"}),
(5400,"EXPAND-2",{"fast_market_max_candidate_checks":"70","product_max_scan_tokens_per_chain":"100","product_new_token_shadow_seconds":"180","product_established_age_seconds":"420"}),
(9000,"EXPAND-3",{"fast_market_max_candidate_checks":"80","product_max_scan_tokens_per_chain":"120","product_new_token_shadow_seconds":"120","product_established_age_seconds":"300"})]
CHANGED_KEYS=sorted({k for _,_,d in STAGES for k in d})

def dec(v,d="0"):
    try:return Decimal(str(v))
    except (InvalidOperation,ValueError,TypeError):return Decimal(str(d))
def boolv(v,d=False):
    if v is None:return d
    return str(v).strip().lower() in {"1","true","yes","on","y"}
def rows(path):
    if not path.exists():return []
    with path.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def set_scoped(path,values,chain_id="*"):
    rs=[];fns=["chain_id","setting","value","description"]
    if path.exists():
        with path.open("r",encoding="utf-8-sig",newline="") as f:
            r=csv.DictReader(f);fns=list(r.fieldnames or fns);rs=list(r)
    for x in ("setting","value","description"):
        if x not in fns:fns.append(x)
    scoped="chain_id" in fns
    prev={k:None for k in values};seen=set()
    for r in rs:
        same=(not scoped) or str(r.get("chain_id","")).strip()==str(chain_id)
        k=str(r.get("setting","")).strip()
        if same and k in values:
            prev[k]=r.get("value");r["value"]=str(values[k]);seen.add(k)
    for k,v in values.items():
        if k in seen:continue
        r={x:"" for x in fns}
        if scoped:r["chain_id"]=str(chain_id)
        r["setting"]=k;r["value"]=str(v);r["description"]="Temporary bounded profit-challenge tuning; safety gates unchanged";rs.append(r)
    tmp=path.with_suffix(path.suffix+".challenge.tmp");path.parent.mkdir(parents=True,exist_ok=True)
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fns);w.writeheader();w.writerows([{x:r.get(x,"") for x in fns} for r in rs]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path);return prev
def restore(path,snap,chain_id="*"):
    if not path.exists():return
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        rd=csv.DictReader(f);fns=list(rd.fieldnames or []);rs=list(rd)
    scoped="chain_id" in fns;out=[]
    for r in rs:
        same=(not scoped) or str(r.get("chain_id","")).strip()==str(chain_id);k=str(r.get("setting","")).strip()
        if same and k in snap:
            if snap[k] is None:continue
            r["value"]=snap[k]
        out.append(r)
    tmp=path.with_suffix(path.suffix+".restore.tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fns);w.writeheader();w.writerows([{x:r.get(x,"") for x in fns} for r in out]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def chats(app):
    out=[str(x).strip() for x in (app.telegram_chat_ids or []) if str(x).strip()]
    for r in rows(Path(app.csv_dir)/"users.csv"):
        if str(r.get("status","")).upper()=="ACTIVE":
            t=str(r.get("telegram_id","")).strip()
            if t:out.append(t)
    return list(dict.fromkeys(out))
def send(app,text):
    cs=chats(app)
    if not app.telegram_bot_token or not cs:
        print("[challenge-telegram] no configured bot token/chat",flush=True);return
    try:
        z=send_to_chats(app.telegram_bot_token,cs,text);print(f"[challenge-telegram] sent={z.get('sent_chats',0)} failed={z.get('failed_chats',0)}",flush=True)
    except Exception as e:print(f"[challenge-telegram-error] {type(e).__name__}: {e}",flush=True)
def prices(cache):
    req=urllib.request.Request(PRICE_URL,headers={"User-Agent":"BOOT-profit-challenge/1.1"})
    try:
        with urllib.request.urlopen(req,timeout=10) as r:data=json.loads(r.read().decode())
        for k in ("ethereum","binancecoin","polygon-ecosystem-token","matic-network"):
            if data.get(k,{}).get("usd") is not None:cache[k]=dec(data[k]["usd"])
    except Exception as e:print(f"[challenge-price-warning] {type(e).__name__}: {e}; using cached prices",flush=True)
    return cache
def chain_price(slug,p):
    slug=str(slug or "").lower()
    if slug=="bsc":return p.get("binancecoin")
    if slug in {"ethereum","base","arbitrum"}:return p.get("ethereum")
    if slug=="polygon":return p.get("polygon-ecosystem-token") or p.get("matic-network")
def pnl(csv_dir,start,p):
    good=[];total=Decimal("0");unpriced={}
    for r in rows(csv_dir/"auto"/"auto_trade_execution.csv"):
        try:ts=int(float(r.get("timestamp_epoch") or 0))
        except:continue
        if ts<start or str(r.get("status","")).upper() not in SUCCESS_STATUSES:continue
        net=dec(r.get("realised_net_base"))-dec(r.get("profit_fee_base"));slug=str(r.get("chain_slug") or "").lower();pr=chain_price(slug,p);usd=net*pr if pr is not None else None
        if usd is not None:total+=usd
        else:unpriced[slug or "unknown"]=unpriced.get(slug or "unknown",Decimal("0"))+net
        good.append({**r,"user_net_base":net,"user_net_usd":usd})
    return {"trades":good,"total_usd":total,"unpriced":unpriced}
def sim_summary(csv_dir,since):
    rr=[]
    for r in rows(csv_dir/"auto"/"auto_trade_simulations.csv"):
        try:ts=int(float(r.get("timestamp_epoch") or 0))
        except:continue
        if ts>=since:rr.append(r)
    ok=sum(boolv(r.get("simulation_ok")) for r in rr);cnt={}
    for r in rr:
        if boolv(r.get("simulation_ok")):continue
        s=str(r.get("reason") or "unknown").strip();s=s if len(s)<=90 else s[:87]+"...";cnt[s]=cnt.get(s,0)+1
    top=[f"{n}x {s}" for s,n in sorted(cnt.items(),key=lambda x:x[1],reverse=True)[:3]]
    return len(rr),ok,top
def fast(csv_dir):
    rr=rows(csv_dir/"auto"/"fast_market_status.csv");return rr[-1] if rr else {}
def product(csv_dir):
    rr=rows(csv_dir/"auto"/"product_universe.csv");o={"tracked":len(rr),"auto":0,"shadow":0,"blocked":0}
    for r in rr:
        a=str(r.get("action") or r.get("auto_action") or r.get("status") or r.get("trade_mode") or "").upper()
        if boolv(r.get("auto_trade")) or boolv(r.get("auto_approved")) or a=="AUTO":o["auto"]+=1
        elif "SHADOW" in a:o["shadow"]+=1
        elif a in {"BLOCK","BLOCKED","QUARANTINE","REJECT"}:o["blocked"]+=1
    return o
def stage(elapsed):
    x=STAGES[0]
    for s in STAGES:
        if elapsed>=s[0]:x=s
        else:break
    return x
def progress(elapsed,duration,target,q,f,pr,sims,name):
    n,ok,reasons=sims;remaining=max(Decimal("0"),target-q["total_usd"])
    L=["🎯 BOOT PROFIT CHALLENGE",f"Stage: {name}",f"Elapsed: {elapsed//60}m / {duration//60}m",f"Realised user net: ${q['total_usd']:.6f}",f"Target: ${target:.4f} | remaining ${remaining:.6f}",f"Successful trades: {len(q['trades'])}",f"Fast scan: routes={f.get('routes','-')} eligible={f.get('eligible','-')} pass={f.get('duration_seconds','-')}s",f"Products: tracked={pr['tracked']} AUTO={pr['auto']} shadow={pr['shadow']} blocked={pr['blocked']}",f"Wallet simulations: {n}, passed={ok}"]
    if reasons:L+=["Top rejects:"]+[f"• {x}" for x in reasons]
    L+=["Safety unchanged: no loss-forcing, no slippage increase, no capital increase, final simulation/eth_call still required."]
    return "\n".join(L)
def write_state(csv_dir,obj):
    p=csv_dir/"auto"/"profit_challenge_status.json";p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(".tmp");t.write_text(json.dumps(obj,indent=2,sort_keys=True));os.replace(t,p)
def run(hours,target,report_minutes,keep):
    app=AppSettings.load();csv_dir=Path(app.csv_dir);sp=csv_dir/"auto_trading_settings.csv";rp=csv_dir/"auto"/"profit_challenge_restore.json";start=int(time.time());duration=max(60,int(hours*Decimal(3600)));deadline=start+duration
    cur=load_kv_scoped(sp,0);snap={k:cur.get(k) for k in CHANGED_KEYS};rp.parent.mkdir(parents=True,exist_ok=True);rp.write_text(json.dumps(snap,indent=2,sort_keys=True))
    stop={"x":False}
    def h(a,b):stop["x"]=True
    signal.signal(signal.SIGINT,h);signal.signal(signal.SIGTERM,h)
    applied=None;last_report=0;last_count=0;window=start;pc={}
    try:
        send(app,f"🚀 BOOT PROFIT CHALLENGE STARTED\nTarget realised user net: ${target}\nMaximum duration: {duration//60} minutes\nGoal alert: ON\nNo capital/slippage increase and no loss-forcing. Profit is not guaranteed.")
        while int(time.time())<deadline and not stop["x"]:
            now=int(time.time());elapsed=now-start;_,name,vals=stage(elapsed)
            if name!=applied:
                set_scoped(sp,vals,"*");applied=name;app=AppSettings.load();send(app,"🛠 BOOT strategy stage changed → "+name+"\n"+", ".join(f"{k}={v}" for k,v in vals.items()))
            pc=prices(pc);q=pnl(csv_dir,start,pc);f=fast(csv_dir);pr=product(csv_dir)
            if len(q["trades"])>last_count:
                for tr in q["trades"][last_count:]:
                    u=tr["user_net_usd"];ut=f"${u:.6f}" if u is not None else "USD price unavailable"
                    send(app,f"✅ CHALLENGE TRADE CONFIRMED\nChain: {str(tr.get('chain_slug') or '').upper()}\nRealised user net: {tr['user_net_base']} native ({ut})\nChallenge total: ${q['total_usd']:.6f}\nTX: {tr.get('tx_hash') or '-'}")
                last_count=len(q["trades"])
            if q["total_usd"]>=target:
                send(app,progress(elapsed,duration,target,q,f,pr,sim_summary(csv_dir,window),applied)+"\n\n🏁 GOAL ACHIEVED — realised user net reached or exceeded the $0.01 target. Challenge stopping.");write_state(csv_dir,{"status":"TARGET_ACHIEVED","realised_user_net_usd":str(q["total_usd"]),"successful_trades":len(q["trades"]),"start_epoch":start,"end_epoch":now});return 0
            if not last_report or now-last_report>=report_minutes*60:
                send(app,progress(elapsed,duration,target,q,f,pr,sim_summary(csv_dir,window),applied));last_report=now;window=now
            write_state(csv_dir,{"status":"RUNNING","target_usd":str(target),"realised_user_net_usd":str(q["total_usd"]),"successful_trades":len(q["trades"]),"stage":applied,"start_epoch":start,"deadline_epoch":deadline,"updated_epoch":now})
            time.sleep(20)
        pc=prices(pc);q=pnl(csv_dir,start,pc);status="STOPPED" if stop["x"] else "DEADLINE";send(app,f"⏹ BOOT PROFIT CHALLENGE {status}\nRealised user net: ${q['total_usd']:.6f}\nTarget: ${target}\nSuccessful trades: {len(q['trades'])}\nThe target was not guaranteed; safeguards were not bypassed.");return 2 if status=="DEADLINE" else 130
    finally:
        if not keep:
            try:restore(sp,snap,"*");send(AppSettings.load(),"↩️ BOOT challenge temporary strategy settings restored.")
            except Exception as e:print("[challenge-restore-error]",e,flush=True)
def restore_only():
    app=AppSettings.load();p=Path(app.csv_dir)/"auto"/"profit_challenge_restore.json"
    if not p.exists():print("No restore snapshot found.");return 1
    restore(Path(app.csv_dir)/"auto_trading_settings.csv",json.loads(p.read_text()),"*");send(app,"↩️ BOOT challenge settings restored from saved snapshot.");return 0
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--hours",type=Decimal,default=Decimal("5"));ap.add_argument("--target-usd",type=Decimal,default=Decimal("0.01"));ap.add_argument("--report-minutes",type=int,default=15);ap.add_argument("--keep-settings",action="store_true");ap.add_argument("--restore",action="store_true");a=ap.parse_args()
    if a.restore:return restore_only()
    if a.hours<=0 or a.target_usd<=0:ap.error("hours and target must be positive")
    return run(a.hours,a.target_usd,max(1,a.report_minutes),a.keep_settings)
if __name__=="__main__":raise SystemExit(main())
