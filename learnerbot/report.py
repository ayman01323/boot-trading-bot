from __future__ import annotations
import hashlib,html
from datetime import datetime,timezone
from .db import get_state
from .multichain import contexts, close_contexts
from .telegram import send_to_chats

def _short(a): return '-' if not a else (a if len(a)<=16 else f"{a[:8]}…{a[-6:]}")
def _link(ctx,a):
    if not a:return '-'
    aa=html.escape(a); label=html.escape(_short(a))
    return f'<a href="{ctx.config.explorer_url}/address/{aa}">{label}</a>' if ctx.config.explorer_url else label

def build_report_html(app):
    ctxs=contexts(app,enabled_only=True,with_rpc=False)
    L=["<b>🤖 MULTI-CHAIN LEARNING BOT — INTELLIGENCE REPORT</b>",f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",f"Enabled chains: <b>{len(ctxs)}</b>",""]
    for c in ctxs:
        last=get_state(c.conn,'last_scanned_block','-'); tx=c.conn.execute('SELECT COUNT(*) n FROM transactions').fetchone()['n']; wc=c.conn.execute('SELECT COUNT(*) n FROM wallet_scores').fetchone()['n']; cand=c.conn.execute('SELECT COUNT(*) n FROM wallet_scores WHERE bot_score>=?',(c.settings.bot_score_threshold,)).fetchone()['n']; sc=c.conn.execute('SELECT COUNT(*) n FROM strategy_patterns').fetchone()['n']; pr=c.conn.execute("SELECT COALESCE(SUM(CASE WHEN net_base>0 THEN net_base ELSE 0 END),0) total,COUNT(*) n FROM profit_evidence WHERE proof_quality='PROVEN_WRAPPED_BASE'").fetchone()
        L += [f"<b>🌐 {html.escape(c.config.name)}</b>",f"Block: <code>{html.escape(str(last))}</code> | tx {tx:,} | wallets {wc:,} | candidates {cand:,} | strategies {sc:,}",f"Positive proven total: <b>{float(pr['total'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)}</b>"]
        leader=c.conn.execute("SELECT * FROM behaviour_rankings WHERE proven_count>0 ORDER BY rank_overall LIMIT 1").fetchone()
        profit_leader=c.conn.execute("SELECT * FROM behaviour_rankings WHERE proven_count>0 ORDER BY total_net_base DESC LIMIT 1").fetchone()
        speed_leader=c.conn.execute("SELECT * FROM behaviour_rankings WHERE proven_count>0 ORDER BY profit_per_hour_base DESC LIMIT 1").fetchone()
        if leader:
            from .behaviour import label
            L.append(f"🏆 Overall research leader: <b>{html.escape(label(leader['behaviour']))}</b> — score {float(leader['overall_score'] or 0):.1f}")
        if profit_leader:
            L.append(f"💵 Highest net behaviour: <b>{html.escape(label(profit_leader['behaviour']))}</b> — {float(profit_leader['total_net_base'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)}")
        if speed_leader:
            L.append(f"⚡ Fastest observed behaviour: <b>{html.escape(label(speed_leader['behaviour']))}</b> — {float(speed_leader['profit_per_hour_base'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)}/h")
        wallets=c.conn.execute('SELECT wallet,bot_score,tx_count FROM wallet_scores WHERE bot_score>=? ORDER BY bot_score DESC LIMIT 3',(c.settings.bot_score_threshold,)).fetchall()
        for r in wallets:L.append(f"• {_link(c,r['wallet'])} — score {r['bot_score']:.1f}, tx {r['tx_count']}")
        L.append('')
    L += ["Use <b>/menu</b> for Wallets, Profit, Highest & Fastest, Behaviours, Strategies, Help and Status.","Mode: <b>READ-ONLY</b>."]
    result='\n'.join(L)
    close_contexts(ctxs)
    return result

def build_report(app):
    import re
    return re.sub(r'<[^>]+>','',build_report_html(app))

def _hash(text): return hashlib.sha256('\n'.join(x for x in text.splitlines() if not x.startswith('Time:')).encode()).hexdigest()
def send_report(app,force=False):
    if not app.telegram_bot_token:return False,'TELEGRAM_BOT_TOKEN is not configured'
    if not app.telegram_chat_ids:return False,'TELEGRAM_CHAT_IDS is not configured'
    text=build_report_html(app); result=send_to_chats(app.telegram_bot_token,app.telegram_chat_ids,text,parse_mode='HTML')
    ok=result['sent_chats']>0 and result['failed_chats']==0
    return ok,f"Telegram report: {result['sent_chats']} chat(s) succeeded, {result['failed_chats']} failed, {result['messages']} message(s) sent"
