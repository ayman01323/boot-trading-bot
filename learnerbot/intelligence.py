from __future__ import annotations
import html,json
from pathlib import Path
from .config import load_kv_scoped
from .telegram import send_to_chats

def _load(path):
    try:return json.loads(path.read_text())
    except:return {"initialized":False,"wallets":{},"profits":{},"strategies":{},"chains":[]}
def _save(path,obj): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(obj,indent=2,sort_keys=True))
def _key(slug,x): return f"{slug}:{x}"
def snapshot(contexts):
    s={"initialized":True,"wallets":{},"profits":{},"strategies":{},"behaviour_leaders":{},"chains":sorted(c.config.slug for c in contexts)}
    for c in contexts:
        for r in c.conn.execute("SELECT wallet,bot_score,tx_count,primary_executor FROM wallet_scores").fetchall():
            s['wallets'][_key(c.config.slug,r['wallet'])]={"score":float(r['bot_score'] or 0),"tx":int(r['tx_count'] or 0),"executor":r['primary_executor']}
        for r in c.conn.execute("""SELECT wallet,SUM(CASE WHEN net_base>0 THEN net_base ELSE 0 END) net FROM profit_evidence WHERE proof_quality='PROVEN_WRAPPED_BASE' GROUP BY wallet HAVING net>0""").fetchall():
            s['profits'][_key(c.config.slug,r['wallet'])]=float(r['net'] or 0)
        for r in c.conn.execute("SELECT pattern_id,strategy_class,confidence,replicability,tx_count,avg_net_base FROM strategy_patterns").fetchall():
            s['strategies'][_key(c.config.slug,r['pattern_id'])]={"class":r['strategy_class'],"confidence":float(r['confidence'] or 0),"replicability":float(r['replicability'] or 0),"tx":int(r['tx_count'] or 0),"avg":r['avg_net_base']}
        leader=c.conn.execute("SELECT behaviour,overall_score,total_net_base,profit_per_hour_base FROM behaviour_rankings WHERE proven_count>0 ORDER BY rank_overall LIMIT 1").fetchone()
        if leader:
            s['behaviour_leaders'][c.config.slug]={"behaviour":leader['behaviour'],"score":float(leader['overall_score'] or 0),"profit":float(leader['total_net_base'] or 0),"speed":float(leader['profit_per_hour_base'] or 0)}
    return s

def _ctxmap(contexts): return {c.config.slug:c for c in contexts}
def _link(ctx,address):
    a=html.escape(address); short=f"{a[:8]}…{a[-6:]}"; return f'<a href="{ctx.config.explorer_url}/address/{a}">{short}</a>' if ctx.config.explorer_url else short

def detect_and_send(app,contexts,initial_silent=True):
    if not app.telegram_bot_token or not app.telegram_chat_ids:return []
    path=app.data_dir/'telegram_intelligence_state.json'; old=_load(path); new=snapshot(contexts); _save(path,new)
    if initial_silent and not old.get('initialized',False): return []
    tg=app.telegram_settings(); cmap=_ctxmap(contexts); alerts=[]
    g=app.general(); wallet_threshold=float(g.get('telegram_min_bot_score','80'))
    master_enabled=str(g.get('telegram_report_enabled','true')).lower() in {'true','1','yes','on'}
    if not master_enabled:return []
    if str(tg.get('auto_alert_chain_config','true')).lower() in {'true','1','yes','on'} and old.get('chains')!=new.get('chains'):
        alerts.append('<b>🌐 CHAIN CONFIG UPDATED</b>\nEnabled now: '+', '.join(new['chains']))
    if str(tg.get('auto_alert_new_wallet','true')).lower() in {'true','1','yes','on'}:
        for key,v in new['wallets'].items():
            prev=old.get('wallets',{}).get(key,{}); prev_score=float(prev.get('score',0) or 0)
            if v['score']>=wallet_threshold and prev_score<wallet_threshold:
                slug,wallet=key.split(':',1); c=cmap.get(slug)
                if c: alerts.append(f"<b>🚨 NEW HIGH-SCORE BOT WALLET</b>\nChain: <b>{html.escape(c.config.name)}</b>\nWallet: {_link(c,wallet)}\nBot score: <b>{v['score']:.1f}/100</b>\nObserved tx: {v['tx']}\nExecutor: {_link(c,v['executor']) if v.get('executor') else '-'}\n\nBot score indicates automation-like behaviour; it does not itself prove profit.")
    if str(tg.get('auto_alert_profit_increase','true')).lower() in {'true','1','yes','on'}:
        for key,v in new['profits'].items():
            slug,wallet=key.split(':',1); c=cmap.get(slug)
            if not c: continue
            risk=load_kv_scoped(app.csv_dir/'risk_settings.csv',c.config.chain_id); minimum=float(risk.get('profit_alert_min_native','0.001'))
            prev=float(old.get('profits',{}).get(key,0) or 0); delta=v-prev
            if prev>0 and delta>=minimum:
                alerts.append(f"<b>📈 PROVEN PROFIT EVIDENCE INCREASE</b>\nChain: <b>{html.escape(c.config.name)}</b>\nWallet: {_link(c,wallet)}\nPrevious: {prev:,.6f} {html.escape(c.config.wrapped_base_symbol)}\nNow: <b>{v:,.6f} {html.escape(c.config.wrapped_base_symbol)}</b>\nChange: <b>+{delta:,.6f}</b>")
            elif prev==0 and v>=minimum:
                alerts.append(f"<b>💰 FIRST POSITIVE PROVEN PROFIT EVIDENCE</b>\nChain: <b>{html.escape(c.config.name)}</b>\nWallet: {_link(c,wallet)}\nPositive evidence total: <b>{v:,.6f} {html.escape(c.config.wrapped_base_symbol)}</b>")
    if str(tg.get('auto_alert_new_strategy','true')).lower() in {'true','1','yes','on'}:
        for key,v in new['strategies'].items():
            if key not in old.get('strategies',{}):
                slug,pid=key.split(':',1); c=cmap.get(slug)
                if c: alerts.append(f"<b>🧠 NEW STRATEGY LEARNED</b>\nChain: <b>{html.escape(c.config.name)}</b>\nStrategy: <b>{html.escape(v['class'])}</b>\nConfidence: {v['confidence']:.1f}/100\nReplicability: {v['replicability']:.1f}/100\nObserved tx: {v['tx']}\n\nUse /strategies to open the detailed explanation.")
    if str(tg.get('auto_alert_strategy_upgrade','true')).lower() in {'true','1','yes','on'}:
        for key,v in new['strategies'].items():
            prev=old.get('strategies',{}).get(key)
            if not prev: continue
            slug,pid=key.split(':',1); c=cmap.get(slug)
            if not c: continue
            risk=load_kv_scoped(app.csv_dir/'risk_settings.csv',c.config.chain_id); d=float(risk.get('strategy_score_alert_delta','5'))
            dc=v['confidence']-float(prev.get('confidence',0)); dr=v['replicability']-float(prev.get('replicability',0))
            if dc>=d or dr>=d:
                alerts.append(f"<b>⭐ STRATEGY EVIDENCE UPGRADED</b>\nChain: <b>{html.escape(c.config.name)}</b>\nStrategy: <b>{html.escape(v['class'])}</b>\nConfidence: {float(prev.get('confidence',0)):.1f} → <b>{v['confidence']:.1f}</b>\nReplicability: {float(prev.get('replicability',0)):.1f} → <b>{v['replicability']:.1f}</b>\nObserved tx: {v['tx']}")
    if str(tg.get('auto_alert_behaviour_leader','false')).lower() in {'true','1','yes','on'}:
        from .behaviour import label
        for slug,v in new.get('behaviour_leaders',{}).items():
            prev=old.get('behaviour_leaders',{}).get(slug)
            if prev and prev.get('behaviour')==v.get('behaviour'):
                continue
            c=cmap.get(slug)
            if c:
                alerts.append(
                    f"<b>🏆 NEW RESEARCH LEADER</b>\nChain: <b>{html.escape(c.config.name)}</b>\n"
                    f"Behaviour: <b>{html.escape(label(v['behaviour']))}</b>\n"
                    f"Overall evidence score: {v['score']:.1f}/100\n"
                    f"Observed net: {v['profit']:.6f} {html.escape(c.config.wrapped_base_symbol)}\n"
                    f"Observed speed: {v['speed']:.6f} {html.escape(c.config.wrapped_base_symbol)}/h\n\n"
                    f"Use /rankings for the full comparison."
                )
    for text in alerts:
        send_to_chats(app.telegram_bot_token,app.telegram_chat_ids,text,parse_mode='HTML')
    return alerts
