from __future__ import annotations
import csv,html,os,threading,time
from .db import get_state
from .config import resolve_dex_label,load_chains,load_kv_scoped
from .multichain import contexts
from .report import build_report_html
from .behaviour import label
from .copy_engine import refresh_copy_candidates,global_top20,generate_recommendations
from .telegram import answer_callback_query,delete_message,get_updates,get_webhook_info,send_message,set_commands
from .operator_control import set_kv,set_scoped_default,set_chain_enabled,set_allowed_behaviours,audit,parse_float
from .execution_queue import queue_summary
from .live_executor import LiveTrader, LiveTradingError, live_wallet_address
from .wallet_store import WalletStore
from .wallet_assets import wallet_assets
from .auto_trader import auto_state
from .multi_wallet_store import MultiWalletStore, MultiWalletError
from .user_registry import ensure_master_seed,get_user,is_master,is_active,join_user,redeem_activation_code,require_user,set_user_setting,user_setting,user_bool,activate_user,create_activation_code
from .fee_engine import fixed_activation_fee,mark_activation_paid,user_fee_plan,master_wallet,fee_plan
from .product_universe import product_rows, universe_summary

def _short(a): return '-' if not a else (a if len(a)<=18 else f"{a[:8]}…{a[-6:]}")
def _link(c,a):
    if not a:return '-'
    aa=html.escape(a); label=html.escape(_short(a)); return f'<a href="{c.config.explorer_url}/address/{aa}">{label}</a>' if c.config.explorer_url else label

def menu_keyboard(app=None, chat_id=None):
    user_rows=[
        [{'text':'🔐 My Wallets & Assets','callback_data':'menu:wallet'},{'text':'💱 My Live Trading','callback_data':'menu:trading'}],
        [{'text':'⚡ My Auto Routes','callback_data':'menu:auto'},{'text':'🛰 Opportunities','callback_data':'menu:opportunities'}],
        [{'text':'🧺 Auto Products','callback_data':'menu:products'},{'text':'🔥 Full Power','callback_data':'menu:power'}],
        [{'text':'📡 Status','callback_data':'menu:status'},{'text':'📖 Help','callback_data':'menu:help'}],
    ]
    if app is not None and chat_id is not None and not _master(app,chat_id):
        return {'inline_keyboard':user_rows}
    return {'inline_keyboard':[
        [{'text':'⚙️ Operator Control','callback_data':'menu:control'}],
        *user_rows,
        [{'text':'🔔 Auto Updates','callback_data':'menu:alerts'}],
        [{'text':'👥 Copy Top 20','callback_data':'menu:copy20'},{'text':'🚦 IN / OUT','callback_data':'menu:signals'}],
        [{'text':'🌐 Chains','callback_data':'menu:chains'},{'text':'🤖 Observed Wallets','callback_data':'menu:wallets'}],
        [{'text':'💰 Wallet Profit','callback_data':'menu:profit'},{'text':'🏆 Highest & Fastest','callback_data':'menu:rankings'}],
        [{'text':'🔬 Trade Behaviours','callback_data':'menu:behaviours'},{'text':'🧠 Strategies','callback_data':'menu:strategies'}],
        [{'text':'📥 Execution Queue','callback_data':'menu:queue'},{'text':'📊 Full Report','callback_data':'menu:report'}]
    ]}
def back_keyboard(): return {'inline_keyboard':[[{'text':'⬅️ Menu','callback_data':'menu:home'}]]}
def _is_on(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {'1','true','yes','on','y'}

def _atomic_set_csv_setting(path,key,value,description=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=[]; fieldnames=['setting','value','description']
    if path.exists():
        with path.open('r',encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames: fieldnames=list(reader.fieldnames)
            rows=list(reader)
    if 'setting' not in fieldnames: fieldnames.insert(0,'setting')
    if 'value' not in fieldnames: fieldnames.append('value')
    if 'description' not in fieldnames: fieldnames.append('description')
    found=False
    for row in rows:
        if (row.get('setting') or '').strip()==key:
            row['value']=str(value).lower();found=True
            if description is not None and not (row.get('description') or '').strip():row['description']=description
            break
    if not found:
        row={k:'' for k in fieldnames};row['setting']=key;row['value']=str(value).lower();row['description']=description or ''
        rows.append(row)
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader();w.writerows(rows)
        f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def _master_updates_on(app): return _is_on(app.general().get('telegram_report_enabled','true'),True)

def alerts_keyboard(app):
    master=_master_updates_on(app); tg=app.telegram_settings()
    def flag(key,default): return _is_on(tg.get(key,'true' if default else 'false'),default)
    def b(label,key,default):
        on=flag(key,default); return {'text':f"{'✅' if on else '❌'} {label}",'callback_data':f'alerts:toggle:{key}'}
    return {'inline_keyboard':[
        [{'text':f"{'🟢' if master else '🔴'} Automatic Updates: {'ON' if master else 'OFF'}",'callback_data':'alerts:master:off' if master else 'alerts:master:on'}],
        [b('Profit Alerts','auto_alert_profit_increase',True),b('New Strategies','auto_alert_new_strategy',True)],
        [b('Strategy Upgrades','auto_alert_strategy_upgrade',True),b('High-Score Wallets','auto_alert_new_wallet',False)],
        [b('Chain Changes','auto_alert_chain_config',True),b('Research Leader','auto_alert_behaviour_leader',False)],
        [{'text':'✅ Enable All Categories','callback_data':'alerts:categories:on'},{'text':'❌ Disable All Categories','callback_data':'alerts:categories:off'}],
        [{'text':'⬅️ Menu','callback_data':'menu:home'}]
    ]}

def alerts_page(app):
    master=_master_updates_on(app); tg=app.telegram_settings(); g=app.general()
    fields=[('Profit evidence increases','auto_alert_profit_increase',True),('New learned strategies','auto_alert_new_strategy',True),('Strategy evidence upgrades','auto_alert_strategy_upgrade',True),('High-score wallets','auto_alert_new_wallet',False),('Enabled-chain changes','auto_alert_chain_config',True),('Highest-ranked behaviour changes','auto_alert_behaviour_leader',False)]
    L=['<b>🔔 TELEGRAM AUTO UPDATES</b>','',f"Master automatic pushes: <b>{'🟢 ON' if master else '🔴 OFF'}</b>",f"Report mode: <b>{html.escape((g.get('telegram_report_mode','meaningful') or 'meaningful').upper())}</b>",'']
    for label,key,default in fields:
        on=_is_on(tg.get(key,'true' if default else 'false'),default);L.append(f"{'✅' if on else '❌'} {html.escape(label)}")
    L += ['','<b>When master is OFF:</b> the bot keeps scanning, learning, ranking and updating its internal Telegram snapshot, but sends no automatic intelligence pushes. Manual commands and menu reports continue to work.','<b>When turned ON again:</b> only new changes from that point are eligible for alerts; old accumulated changes are not replayed.','', '<i>This is a global control for all authorised Telegram chat IDs configured for this bot.</i>']
    return '\n'.join(L)

_ALERT_DESCRIPTIONS={
 'auto_alert_profit_increase':'Alert on meaningful proven-profit increase',
 'auto_alert_new_strategy':'Alert when a new strategy is learned',
 'auto_alert_strategy_upgrade':'Alert when confidence/replicability rises materially',
 'auto_alert_new_wallet':'Alert when a new wallet crosses telegram_min_bot_score',
 'auto_alert_chain_config':'Alert when enabled-chain configuration changes',
 'auto_alert_behaviour_leader':'Alert when the highest-ranked behaviour changes',
}

def _set_master_updates(app,on):
    _atomic_set_csv_setting(app.csv_dir/'general_settings.csv','telegram_report_enabled','true' if on else 'false','Enable Telegram intelligence output')

def _set_alert_category(app,key,on):
    if key not in _ALERT_DESCRIPTIONS: raise ValueError('Unknown alert category')
    _atomic_set_csv_setting(app.csv_dir/'telegram_settings.csv',key,'true' if on else 'false',_ALERT_DESCRIPTIONS[key])

def _toggle_alert_category(app,key):
    if key not in _ALERT_DESCRIPTIONS: raise ValueError('Unknown alert category')
    current=_is_on(app.telegram_settings().get(key,'false'),False);_set_alert_category(app,key,not current);return not current
def home_text(): return '<b>🤖 Multi-Chain Learning Bot v2.3 — FULL POWER V2/V3 + Multi-User</b>\n\nEach Telegram ID has isolated wallets, assets, transfers and trading settings. The master can configure fee plans and platform controls, while users cannot decrypt or control one another’s wallets. The shared opportunity engine combines learned-wallet research with an independent fast graph-first current-market V2 scanner on every enabled EVM chain. Execution is simulated separately for each user wallet, and every automatic route is re-quoted and re-simulated immediately before signing.\n\n<b>Security:</b> private keys are encrypted server-side and never written to CSV. Imported-key messages must be deleted by Telegram before the key is persisted. Use dedicated low-capital wallets.'

def _ctxs(app): return contexts(app,enabled_only=True,with_rpc=False)
def chains_page(app):
    L=['<b>🌐 ENABLED CHAINS</b>','']
    for c in _ctxs(app):
        rpc='configured' if c.config.rpc_urls else 'MISSING'
        L.append(f"• <b>{html.escape(c.config.name)}</b> — chain {c.config.chain_id}, RPC {rpc}, base {html.escape(c.config.wrapped_base_symbol)}")
    L += ['', 'Enable/disable chains in <code>CSVbot/chains.csv</code>. RPCs are controlled by <code>CSVbot/rpc_endpoints.csv</code>.']
    return '\n'.join(L)
def wallets_page(app):
    L=['<b>🤖 BOT WALLET SCAN — ALL CHAINS</b>','']
    found=0
    for c in _ctxs(app):
        rows=c.conn.execute('SELECT wallet,bot_score,tx_count,tx_per_min,primary_executor FROM wallet_scores WHERE bot_score>=? ORDER BY bot_score DESC LIMIT 8',(c.settings.bot_score_threshold,)).fetchall()
        if not rows: continue
        L.append(f"<b>🌐 {html.escape(c.config.name)}</b>")
        for r in rows:
            found+=1; L.append(f"• {_link(c,r['wallet'])} — score <b>{r['bot_score']:.1f}</b>, tx {r['tx_count']}, {r['tx_per_min']:.2f}/min, exec {_link(c,r['primary_executor'])}")
        L.append('')
    if not found:L.append('No wallet is above the bot-score threshold yet.')
    L.append('A high bot score indicates automation-like behaviour; it does not by itself prove profitability.')
    return '\n'.join(L)
def profit_page(app):
    L=['<b>💰 WALLET PROFIT RESEARCH — ALL CHAINS</b>',
       'Only closed-cycle <b>PROVEN_WRAPPED_BASE</b> evidence enters these net-profit figures.','']
    found=0
    for c in _ctxs(app):
        rows=c.conn.execute(
            """SELECT wallet,
                      SUM(total_net_base) net,
                      SUM(proven_count) proven,
                      SUM(positive_count) positive,
                      SUM(negative_count) negative,
                      MAX(profit_per_hour_base) best_hourly
               FROM wallet_behaviour_rankings
               WHERE proven_count>0
               GROUP BY wallet
               HAVING net!=0
               ORDER BY net DESC LIMIT 8"""
        ).fetchall()
        if not rows: continue
        L.append(f"<b>🌐 {html.escape(c.config.name)}</b>")
        for r in rows:
            found+=1
            ratio=(float(r['positive'] or 0)/float(r['proven'] or 1))*100
            L.append(
                f"• {_link(c,r['wallet'])} — net <b>{float(r['net']):,.6f} "
                f"{html.escape(c.config.wrapped_base_symbol)}</b> | "
                f"positive {ratio:.1f}% | fastest observed behaviour "
                f"{float(r['best_hourly'] or 0):,.6f}/h"
            )
        L.append('')
    if not found:
        L.append('No closed-cycle wallet profit evidence has been ranked yet.')
    L += [
        '<b>Important:</b> capital transfers, treasury sweeps and unmatched token entries/exits are excluded from the profit leaderboard.',
        'Use <b>🏆 Highest & Fastest</b> to compare trade behaviours rather than individual wallets.'
    ]
    return '\n'.join(L)

def behaviours_page(app):
    L=['<b>🔬 TRADE / BEHAVIOUR RESEARCH</b>',
       'The bot separates <b>classification</b> from <b>profit proof</b>. A candidate can be monitored without being allowed onto the profit leaderboard.','']
    seen=0
    for c in _ctxs(app):
        rows=c.conn.execute(
            """SELECT behaviour,evidence_count,wallet_count,proven_count,
                      positive_count,negative_count,total_net_base,
                      profit_per_hour_base,positive_ratio,overall_score
               FROM behaviour_rankings
               ORDER BY evidence_count DESC,overall_score DESC LIMIT 12"""
        ).fetchall()
        if not rows: continue
        L.append(f"<b>🌐 {html.escape(c.config.name)}</b>")
        for r in rows:
            seen+=1
            proof='✅ ranked' if int(r['proven_count'] or 0)>0 else '🔎 research only'
            L.append(
                f"• <b>{html.escape(label(r['behaviour']))}</b> — {proof} | "
                f"evidence {r['evidence_count']} | wallets {r['wallet_count']} | "
                f"closed-cycle proof {r['proven_count']}"
            )
        L.append('')
    if not seen:
        L.append('No behaviour research has been generated yet.')
    L += [
        '<b>Monitored families:</b> triangular/multi-hop arbitrage, two-asset arbitrage, stablecoin arbitrage, private-routed arbitrage, liquidation, LP/liquidity management, market making, momentum/swing, bridge/cross-chain, automated executors and treasury transfers.',
        'Liquidation/LP/bridge rules can be added safely through <code>CSVbot/behaviour_registry.csv</code> without changing Python code.'
    ]
    return '\n'.join(L)

def rankings_page(app):
    L=['<b>🏆 HIGHEST & FASTEST PROFIT RESEARCH</b>',
       'Rankings use only closed-cycle wrapped-base evidence. <b>Profit/hour is historical observed speed, not a forecast.</b>','']
    found=0
    for c in _ctxs(app):
        rows=c.conn.execute(
            """SELECT * FROM behaviour_rankings
               WHERE proven_count>0
               ORDER BY rank_overall,rank_profit,rank_speed LIMIT 8"""
        ).fetchall()
        if not rows: continue
        found+=len(rows)
        L.append(f"<b>🌐 {html.escape(c.config.name)}</b>")
        # leaders
        best_profit=min(rows,key=lambda r:int(r['rank_profit'] or 999999))
        best_speed=min(rows,key=lambda r:int(r['rank_speed'] or 999999))
        best_overall=min(rows,key=lambda r:int(r['rank_overall'] or 999999))
        L += [
            f"💵 <b>Highest net:</b> {html.escape(label(best_profit['behaviour']))} — "
            f"{float(best_profit['total_net_base'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)}",
            f"⚡ <b>Fastest:</b> {html.escape(label(best_speed['behaviour']))} — "
            f"{float(best_speed['profit_per_hour_base'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)}/h",
            f"🏆 <b>Best combined evidence:</b> {html.escape(label(best_overall['behaviour']))} — "
            f"score {float(best_overall['overall_score'] or 0):.1f}/100",
            ''
        ]
        for r in rows[:5]:
            L.append(
                f"#{int(r['rank_overall'] or 0)} <b>{html.escape(label(r['behaviour']))}</b>\n"
                f"   net {float(r['total_net_base'] or 0):,.6f} {html.escape(c.config.wrapped_base_symbol)} | "
                f"{float(r['profit_per_hour_base'] or 0):,.6f}/h | "
                f"positive {float(r['positive_ratio'] or 0)*100:.1f}% | "
                f"proof {r['proven_count']} | overall {float(r['overall_score'] or 0):.1f}"
            )
        L.append('')
    if not found:
        L.append('No behaviour has enough closed-cycle proof to rank yet.')
    L += [
        '<b>How “fastest” is calculated:</b> net proven wrapped-base result divided by the observed active research window, with a minimum time window to avoid one isolated trade appearing infinitely fast.',
        '<b>Do not interpret the ranking as a promise of future returns.</b> It is a research priority list for shadow testing.'
    ]
    return '\n'.join(L)

def copy20_page(app):
    ctxs=_ctxs(app)
    for c in ctxs: refresh_copy_candidates(c.conn,c.settings)
    rows=global_top20(ctxs,app.csv_dir)
    L=['<b>👥 TOP 20 APPROVED COPY-RESEARCH WALLETS</b>',
       'Passed historical profitability, evidence, consistency, loss and copyability checks. '
       '<b>Approval means learn/shadow the behaviour; it does not guarantee the next trade.</b>','']
    if not rows:
        L.append('No wallet currently passes all copy checks.');return '\n'.join(L)
    cmap={c.config.chain_id:c for c in ctxs}
    for r in rows:
        c=cmap.get(int(r['chain_id']));wallet=_link(c,r['wallet']) if c else html.escape(_short(r['wallet']))
        L += [f"<b>#{r['global_rank']} {html.escape(r['chain_slug'].upper())}</b> {wallet}",
              f"Behaviour: <b>{html.escape(label(r['behaviour']))}</b>",
              f"Copy score: <b>{float(r['copy_score']):.1f}/100</b> | bot {float(r['bot_score']):.1f} | confidence {float(r['avg_behaviour_confidence']):.1f}",
              f"Closed cycles: {int(r['proven_count'])} | positive {float(r['positive_ratio'])*100:.1f}% | net {float(r['total_net_base']):,.6f} {html.escape(r['wrapped_base_symbol'])}",
              f"Observed speed: {float(r['profit_per_hour_base']):,.6f} {html.escape(r['wrapped_base_symbol'])}/h",
              '<b>Qualification: 🟢 approved for live re-check / shadow learning</b>','']
    L += ['Global ordering uses threshold-normalised scores rather than directly treating BNB and ETH as the same unit.',
          'Use <b>🚦 IN / OUT</b> for current opportunity recommendations.']
    return '\n'.join(L)

def signals_page(app):
    ctxs=_ctxs(app)
    for c in ctxs: refresh_copy_candidates(c.conn,c.settings)
    _,recs=generate_recommendations(ctxs,app.csv_dir)
    L=['<b>🚦 COPY TRADE IN / OUT — ADVISORY</b>',
       '<b>IN</b> requires Top-20 approval plus every configured current-market gate. SHADOW only reports; ARMED only writes a local execution-queue row.','']
    if not recs:
        L += ['No current live-opportunity rows are available.',
              'Populate <code>CSVbot/live_opportunities.csv</code> from a live exact-quote/simulation scanner.',
              'Until then: <b>OUT / do not copy an already-mined transaction directly.</b>']
        return '\n'.join(L)
    for r in recs[:20]:
        icon='🟢' if r['action']=='IN' else ('🔴' if r['action']=='OUT' else '⚪')
        c=next((x for x in ctxs if x.config.chain_id==int(r['chain_id'])),None)
        wallet=_link(c,r['wallet']) if c else html.escape(_short(r['wallet']));sym=html.escape(c.config.wrapped_base_symbol) if c else 'BASE'
        L += [f"{icon} <b>{r['action']} — {html.escape(r['chain_slug'].upper())}</b>",
              f"Wallet: {wallet}",f"Behaviour: {html.escape(label(r['behaviour']))}",f"Route: <code>{html.escape(r['route_id'])}</code>",
              f"Recommended input: {float(r['recommended_input_base']):,.6f} {sym}",
              f"Conservative net: <b>{float(r['conservative_net_profit_base']):,.6f} {sym}</b>",
              f"Checks: {r['checks_passed']} passed / {r['checks_failed']} failed",f"Reason: {html.escape(r['reason'])}",'']
    L += ['Default follower haircut: 25% of source sizing and 50% of source gross edge, then deduct our gas, builder fee and slippage reserve.',
          '<b>IN is a recommendation, not a profit guarantee.</b>']
    return '\n'.join(L)

HELP={
'TRIANGULAR_OR_MULTI_HOP_ARBITRAGE_CANDIDATE':('Multi-hop/triangular arbitrage candidate','Several token/pool legs appear to return value to the wrapped base asset. Profit can arise from temporary price inconsistencies that exceed fees, gas, builder payment and slippage.'),
'TWO_ASSET_ARBITRAGE_CANDIDATE':('Two-asset arbitrage candidate','A round trip appears to exploit an effective price difference between venues/pools and return more wrapped base after costs.'),
'AUTOMATED_EXECUTOR_PATTERN':('Automated executor pattern','Repeated contract/selector activity is visible, but current evidence does not prove arbitrage. Treat it as a research hypothesis.'),
'TRANSFER_OR_TREASURY_PATTERN':('Transfer/treasury pattern','The flow looks more like collection or treasury movement. Incoming transfers are not automatically trading profit.'),
'NO_TOKEN_FLOW':('No token-flow route','The receipt did not provide a useful token route; internal/native tracing may be required.')}

def strategies_page(app):
    L=['<b>🧠 LEARNED STRATEGIES — ALL CHAINS</b>','Tap a strategy button for the explanation.','']; buttons=[]; n=0
    for c in _ctxs(app):
        rows=c.conn.execute('SELECT * FROM strategy_patterns ORDER BY replicability DESC,confidence DESC LIMIT 6').fetchall()
        if not rows: continue
        L.append(f"<b>🌐 {html.escape(c.config.name)}</b>")
        for r in rows:
            n+=1; title,desc=HELP.get(r['strategy_class'],(r['strategy_class'],'Repeated on-chain execution pattern requiring further validation.'))
            L.append(f"{n}. <b>{html.escape(title)}</b> — repl {r['replicability']:.1f}, conf {r['confidence']:.1f}, tx {r['tx_count']}, exec {_link(c,r['executor'])}")
            buttons.append([{'text':f'🧠 {c.config.slug.upper()} Strategy {n}','callback_data':f"strategy:{c.config.slug}:{r['pattern_id']}"}])
        L.append('')
    if not n:L.append('No repeated strategy has passed the minimum observation threshold yet.')
    buttons.append([{'text':'📖 How to read strategies','callback_data':'menu:help'},{'text':'⬅️ Menu','callback_data':'menu:home'}])
    return '\n'.join(L),{'inline_keyboard':buttons}
def strategy_detail(app,slug,pid):
    c=next((x for x in _ctxs(app) if x.config.slug==slug),None)
    if not c:return 'Chain not enabled.',back_keyboard()
    r=c.conn.execute('SELECT * FROM strategy_patterns WHERE pattern_id=?',(pid,)).fetchone()
    if not r:return 'Strategy record not found.',back_keyboard()
    title,desc=HELP.get(r['strategy_class'],(r['strategy_class'],'Repeated on-chain execution pattern requiring further validation.'))
    route=(r['route_fingerprint'] or '').split('|'); tokens=[x for x in (route[2].split('>') if len(route)>=3 else []) if x.startswith('0x')]
    avg_net_text = '-' if r['avg_net_base'] is None else f"{float(r['avg_net_base']):,.8f}"
    L=[f"<b>🧠 STRATEGY DETAIL — {html.escape(c.config.name)}</b>",f"<b>{html.escape(title)}</b>",'', '<b>What it appears to do</b>',html.escape(desc),'','<b>Evidence</b>',f"Observed tx: {r['tx_count']} | wallets: {r['wallet_count']}",f"Proven-profit rows: {r['proven_profit_count']} | positive: {r['positive_count']}",f"Confidence: <b>{r['confidence']:.1f}/100</b>",f"Replicability: <b>{r['replicability']:.1f}/100</b>",f"Average positive net base: {avg_net_text} {html.escape(r['base_symbol'] or c.config.wrapped_base_symbol)}",'', '<b>Execution fingerprint</b>',f"Executor: {_link(c,r['executor'])}",f"DEX attribution: {html.escape(resolve_dex_label(app.csv_dir,c.config.chain_id,r['executor']) or 'unattributed')}",f"Selector: <code>{html.escape(r['selector'] or '-')}</code>"]
    if tokens:L += ['', '<b>Observed token route fingerprint</b>',' → '.join(f'<a href="{c.config.explorer_url}/token/{html.escape(x)}">{html.escape(_short(x))}</a>' for x in tokens[:10])]
    L += ['', '<b>How to use this</b>','Learn the recurring route, pools/DEXs, sizing, timing and minimum edge. Do not copy an already-mined transaction. Re-scan current pool state and shadow-test independently.','', '<b>Important</b>','Confidence is classification evidence strength, not future win probability. Replicability is not expected ROI.']
    return '\n'.join(L),back_keyboard()
def help_page(): return '<b>📖 STRATEGY & PROFIT HELP</b>\n\n<b>Bot score</b>: automation-likeness, not profit.\n\n<b>PROVEN_WRAPPED_BASE</b>: positive wrapped-native boundary delta after gas and recognised builder payments. Stronger than an incoming transfer, but not a full audit.\n\n<b>Confidence</b>: strength of repeated evidence supporting classification; not win rate.\n\n<b>Replicability</b>: how reproducible the mechanics appear; not ROI.\n\n<b>Arbitrage</b>: current pool prices must create a spread larger than DEX fees, gas, builder cost and slippage. Historic transactions are evidence for a method, not a signal to copy after mining.\n\n<b>Highest profit</b>: net closed-cycle wrapped-base evidence accumulated for a behaviour.\n\n<b>Fastest profit</b>: net evidence divided by the observed active time window. It is historical research speed, not a future rate.\n\n<b>Positive %</b>: positive closed-cycle evidence rows divided by all closed-cycle evidence rows for that behaviour.\n\n<b>Research only</b>: the behaviour was detected but profit is not yet strong enough to rank.\n\n<b>Copy Top 20</b>: wallet behaviour must pass profitability, evidence, consistency, loss, automation and classification-confidence gates.\n\n<b>IN / OUT</b>: IN additionally requires fresh exact quote, simulation, liquidity, sellability, route approval, input cap, conservative minimum profit and atomic profit protection. OUT means a current gate failed or the signal is stale. ARMED writes qualifying IN signals to a local queue but does not sign them.\n\n<b>Follower haircut</b>: default sizing is 25% of source size and only 50% of source gross edge is assumed to survive before our costs.\n\n<b>Cross-chain rule</b>: the same 0x address is analysed independently on each chain.'
_PENDING_INPUT={}
_BEHAVIOURS=[
    ('TRIANGULAR_MULTI_HOP_ARBITRAGE','Triangular / multi-hop'),
    ('TWO_ASSET_ARBITRAGE','Two-asset arbitrage'),
    ('STABLECOIN_ARBITRAGE','Stablecoin arbitrage'),
    ('PRIVATE_ROUTED_ARBITRAGE','Private-routed arbitrage'),
]
_PARAM_SPECS={
    'max_copy_input_base':('Maximum input (wrapped-base units)',0.000001,1000000.0,'max_copy_input_base'),
    'min_conservative_profit_base':('Minimum conservative profit',0.0,1000000.0,'min_conservative_profit_base'),
    'copy_size_pct_of_source':('Copy size % of source',0.1,100.0,'copy_size_pct_of_source'),
    'copy_edge_capture_pct':('Follower edge capture %',0.1,100.0,'copy_edge_capture_pct'),
    'max_signal_age_seconds':('Maximum signal age in seconds',0.1,3600.0,'max_signal_age_seconds'),
    'canary_input_base':('Canary input (wrapped-base units)',0.000001,1000000.0,'canary_input_base'),
    'min_copy_score':('Minimum copy score',0.0,100.0,'min_copy_score'),
}

def _operator_on(app):
    return _is_on(app.operator_settings().get('engine_enabled','true'),True)

def _telegram_write_on(app):
    return _is_on(app.operator_settings().get('telegram_write_enabled','true'),True)

def _copy_cfg(app):
    return load_kv_scoped(app.csv_dir/'copy_settings.csv',0)

def control_page(app):
    cfg=_copy_cfg(app); op=app.operator_settings(); mode=(cfg.get('recommendation_mode','SHADOW') or 'SHADOW').upper()
    chains=load_chains(app,enabled_only=False); allowed=set(x for x in (cfg.get('allowed_behaviours','') or '').split('|') if x)
    L=['<b>⚙️ OPERATOR CONTROL — v2.3</b>','',f"Engine: <b>{'🟢 ON' if _operator_on(app) else '🔴 PAUSED'}</b>",f"Telegram writes: <b>{'ON' if _telegram_write_on(app) else 'OFF'}</b>",f"Recommendation mode: <b>{html.escape(mode)}</b>",f"Execution queue: <b>{'ON' if _is_on(op.get('execution_queue_enabled','true'),True) else 'OFF'}</b>",'', '<b>Capital / trade gates</b>',f"Maximum input: <b>{html.escape(cfg.get('max_copy_input_base','1'))}</b> wrapped-base",f"Canary input: <b>{html.escape(cfg.get('canary_input_base','0.05'))}</b>",f"Minimum conservative profit: <b>{html.escape(cfg.get('min_conservative_profit_base','0.001'))}</b>",f"Copy size: <b>{html.escape(cfg.get('copy_size_pct_of_source','25'))}%</b> of source",f"Edge capture assumption: <b>{html.escape(cfg.get('copy_edge_capture_pct','50'))}%</b>",f"Maximum signal age: <b>{html.escape(cfg.get('max_signal_age_seconds','2'))} sec</b>",f"Minimum copy score: <b>{html.escape(cfg.get('min_copy_score','65'))}</b>",'', '<b>Chains</b>']
    for c in chains:L.append(f"{'✅' if c.enabled else '❌'} {html.escape(c.name)}")
    L += ['', '<b>Eligible arbitrage behaviours</b>']
    for key,name in _BEHAVIOURS:L.append(f"{'✅' if key in allowed else '❌'} {html.escape(name)}")
    L += ['', '<i>SHADOW only reports. ARMED can place an IN recommendation into the local execution queue; a separate local executor must independently re-quote and re-simulate before signing.</i>']
    return '\n'.join(L)

def control_keyboard(app):
    cfg=_copy_cfg(app); mode=(cfg.get('recommendation_mode','SHADOW') or 'SHADOW').upper(); chains=load_chains(app,enabled_only=False)
    rows=[
        [{'text':f"{'🟢' if _operator_on(app) else '🔴'} Engine {'ON' if _operator_on(app) else 'PAUSED'}",'callback_data':'control:engine:off' if _operator_on(app) else 'control:engine:on'}],
        [{'text':f"{'✅' if mode=='SHADOW' else '▫️'} SHADOW",'callback_data':'control:mode:SHADOW'},{'text':f"{'✅' if mode=='ARMED' else '▫️'} ARMED",'callback_data':'control:mode:ARMED'}],
    ]
    chainrow=[]
    for c in chains:
        chainrow.append({'text':f"{'✅' if c.enabled else '❌'} {c.slug.upper()}",'callback_data':f"control:chain:{c.chain_id}:{'off' if c.enabled else 'on'}"})
        if len(chainrow)==2:rows.append(chainrow);chainrow=[]
    if chainrow:rows.append(chainrow)
    rows += [
        [{'text':'🔬 Select Arbitrage Behaviours','callback_data':'control:behaviours'}],
        [{'text':'💼 Set Max Input','callback_data':'control:ask:max_copy_input_base'},{'text':'🎯 Set Min Profit','callback_data':'control:ask:min_conservative_profit_base'}],
        [{'text':'📐 Set Copy %','callback_data':'control:ask:copy_size_pct_of_source'},{'text':'✂️ Set Edge %','callback_data':'control:ask:copy_edge_capture_pct'}],
        [{'text':'⏱ Set Signal Age','callback_data':'control:ask:max_signal_age_seconds'},{'text':'🐣 Set Canary','callback_data':'control:ask:canary_input_base'}],
        [{'text':'⭐ Set Min Score','callback_data':'control:ask:min_copy_score'},{'text':'📥 Queue','callback_data':'menu:queue'}],
        [{'text':'⬅️ Menu','callback_data':'menu:home'}]
    ]
    return {'inline_keyboard':rows}

def behaviours_control_page(app):
    cfg=_copy_cfg(app); allowed=set(x for x in (cfg.get('allowed_behaviours','') or '').split('|') if x)
    L=['<b>🔬 COPY-ELIGIBLE ARBITRAGE BEHAVIOURS</b>','','Toggle which historical behaviours may qualify for Top-20 copy research. This does not make a historical transaction a live signal.','']
    rows=[]
    for key,name in _BEHAVIOURS:
        on=key in allowed;L.append(f"{'✅' if on else '❌'} {html.escape(name)}")
        rows.append([{'text':f"{'✅' if on else '❌'} {name}",'callback_data':f'control:behaviour:{key}'}])
    rows.append([{'text':'⬅️ Operator Control','callback_data':'menu:control'}])
    return '\n'.join(L),{'inline_keyboard':rows}

def queue_page(app):
    q=queue_summary(app.csv_dir); L=['<b>📥 LOCAL EXECUTION QUEUE</b>','',f"Rows: <b>{q['total']}</b> | pending local executor: <b>{q['pending']}</b>",'']
    if not q['recent']:L.append('Queue is empty.')
    else:
        for r in reversed(q['recent'][-8:]):
            L += [f"• <b>{html.escape((r.get('chain_slug') or '').upper())}</b> <code>{html.escape(_short(r.get('wallet') or ''))}</code> — {html.escape(r.get('status') or '')}",f"  route <code>{html.escape(r.get('route_id') or '-')}</code> | input {html.escape(str(r.get('recommended_input_base') or '-'))}"]
    L += ['','<b>v2.3 never auto-signs an historical transaction.</b> The auto engine signs only a fresh graph/learned scanner route that has an exact current quote, whole-route simulation, liquidity check and atomic minimum-output profit floor.']
    return '\n'.join(L)

def _live_cfg(app):
    return load_kv_scoped(app.csv_dir/'live_trading_settings.csv',0)

def _file_rows(path):
    if not path.exists():return []
    with path.open('r',encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def wallet_page(app, chat_id):
    u=require_user(app.csv_dir,chat_id,active=False); store=MultiWalletStore(app.data_dir,app.csv_dir); wallets=store.list_wallets(chat_id)
    L=['<b>🔐 MY WALLETS — v2.3</b>','',f"User: <code>{html.escape(str(chat_id))}</code> | status <b>{html.escape((u.get('status') or '').upper())}</b> | plan <b>{html.escape(u.get('fee_plan_id') or '-')}</b>"]
    if not wallets:L += ['','No wallet configured.']
    for r in wallets:
        mark='✅ ACTIVE' if _is_on(r.get('active'),False) else '▫️'
        L += [f"{mark} <b>{html.escape(r.get('label') or r.get('wallet_id') or '')}</b> — <code>{html.escape(r.get('wallet_id') or '')}</code>",f"Address: <code>{html.escape(r.get('address') or '')}</code>"]
    L += ['','<b>Wallet commands</b>','<code>/walletcreate CONFIRM</code>','<code>/walletcreate Bot2 CONFIRM</code>','<code>/walletimport 0xPRIVATEKEY CONFIRM</code>','<code>/walletimport Bot2 0xPRIVATEKEY CONFIRM</code>','<code>/walletuse w1234abcd</code>','<code>/walletremove w1234abcd CONFIRM</code>','<code>/assets bsc</code>','<code>/transfer bsc native 0xTO 0.001 CONFIRM</code>','<code>/transfer bsc 0xTOKEN 0xTO 50% CONFIRM</code>','', '<b>Isolation:</b> commands always resolve wallets using the sender’s Telegram ID. MASTER can see public platform metadata but cannot decrypt or sign with another user’s wallet through Telegram.']
    return '\n'.join(L)

def _auto_cfg(app):return load_kv_scoped(app.csv_dir/'auto_trading_settings.csv',0)

def auto_page(app, chat_id):
    u=require_user(app.csv_dir,chat_id,active=False); cfg=_auto_cfg(app); st=auto_state(app.csv_dir,chat_id); store=MultiWalletStore(app.data_dir,app.csv_dir)
    live_on=user_bool(app.csv_dir,chat_id,0,'live_trading_enabled',False); auto_on=user_bool(app.csv_dir,chat_id,0,'auto_trading_enabled',False); mode=str(user_setting(app.csv_dir,chat_id,0,'recommendation_mode','SHADOW')).upper()
    amount=user_setting(app.csv_dir,chat_id,0,'auto_input_base',cfg.get('auto_input_base','0.005'));minp=user_setting(app.csv_dir,chat_id,0,'min_net_profit_base',cfg.get('min_net_profit_base','0.0002'))
    platform=_is_on(cfg.get('auto_trading_enabled','false'),False)
    L=['<b>⚡ MY AUTOMATIC ROUTE ENGINE — v2.3 FULL POWER</b>','',f"Account: <b>{html.escape((u.get('status') or '').upper())}</b> | wallet: <b>{'configured' if store.has_wallet(chat_id) else 'missing'}</b>",f"Platform auto gate: <b>{'ON' if platform else 'OFF'}</b>",f"Platform LIVE gate: <b>{'ON' if _is_on(_live_cfg(app).get('trading_enabled','false'),False) else 'OFF'}</b>",f"My LIVE: <b>{'ON' if live_on else 'OFF'}</b>",f"My AUTOTRADE: <b>{'ON' if auto_on else 'OFF'}</b>",f"My mode: <b>{html.escape(mode)}</b>",f"My route input: <b>{html.escape(str(amount))}</b>",f"My minimum retained net: <b>{html.escape(str(minp))}</b>",f"Trades last hour: <b>{st['hour_trades']}</b>",'','<code>/opportunities</code>','<code>/autoprep bsc 0.01 CONFIRM</code>','<code>/setautosize 0.005</code>','<code>/setautoprofit 0.0002</code>','<code>/mode armed</code>','<code>/live on CONFIRM</code>','<code>/autotrade on CONFIRM</code>','<code>/autotrade off</code>','', '<i>Execution needs platform gate + ACTIVE user + selected wallet + user LIVE + ARMED + AUTOTRADE. Every route is re-quoted and simulated for your selected wallet immediately before signing.</i>']
    return '\n'.join(L)

def opportunities_page(app, chat_id):
    require_user(app.csv_dir,chat_id,active=False); rows=_file_rows(app.csv_dir/'live_opportunities.csv'); rows=sorted(rows,key=lambda r:float(r.get('expected_gross_profit_base') or 0)-float(r.get('slippage_reserve_base') or 0),reverse=True)[:8]
    L=['<b>🛰 FRESH ROUTES FOR MY WALLET</b>','']
    if not rows:return '\n'.join(L+['No fresh route candidates yet.'])
    store=MultiWalletStore(app.data_dir,app.csv_dir)
    if not store.has_wallet(chat_id):return '\n'.join(L+['Create/import a wallet first.'])
    for r in rows:
        slug=(r.get('chain_slug') or '').lower();path=[x for x in (r.get('route_path') or '').split('>') if x]
        try:
            kind=str(r.get('route_kind') or 'V2_CYCLE').upper();t=LiveTrader(app,slug,telegram_id=chat_id,router_override=((r.get("router_address") or None) if kind=='V2_CYCLE' else None));amount=user_setting(app.csv_dir,chat_id,t.chain.chain_id,'auto_input_base',_auto_cfg(app).get('auto_input_base','0.005'));minp=user_setting(app.csv_dir,chat_id,t.chain.chain_id,'min_net_profit_base',_auto_cfg(app).get('min_net_profit_base','0.0002'))
            if kind=='V3_CYCLE':
                fees=[int(x) for x in str(r.get('route_fees') or '').split('>') if x.strip()];sim=t.simulate_v3_cycle(path,fees,amount,minp,r.get('router_address'),r.get('quoter_address'))
            elif kind.startswith('CROSS_'):
                raise ValueError('Cross-DEX candidate is shadow-only until the atomic multi-router executor is deployed')
            else:sim=t.simulate_cycle(path,amount,minp)
            ok=bool(sim.get('simulation_ok'));net=float(sim.get('gross_profit') or 0)-float(sim.get('gas_cost_base') or 0)
            source='DIRECT' if str(r.get('wallet') or '')=='DIRECT_MARKET' else 'LEARNED';behaviour=str(r.get('behaviour') or '')
            L += [f"{'🟢' if ok else '⚪'} <b>{html.escape(slug.upper())}</b> [{source}/{html.escape(kind)}] wallet-net-before-platform-fee ≈ <b>{net:.8f}</b>",f"<code>{html.escape(' → '.join(_short(x) for x in path))}</code>",f"type={html.escape(behaviour[:45])} exact=true liquidity={html.escape(r.get('liquidity_ok') or '')} my_sim={str(ok).lower()} reason={html.escape(str(sim.get('reason') or '')[:180])}",'']
        except Exception as e:L += [f"⚪ <b>{html.escape(slug.upper())}</b> — {html.escape(str(e)[:180])}",'']
    return '\n'.join(L)

def power_page(app,chat_id):
    require_user(app.csv_dir,chat_id,active=False)
    status=_file_rows(app.csv_dir/'auto'/'fast_market_status.csv');st=status[-1] if status else {}
    rows=_file_rows(app.csv_dir/'auto'/'full_power_opportunities.csv')
    counts={}
    for r in rows:
        k=str(r.get('route_kind') or 'V2_CYCLE');counts[k]=counts.get(k,0)+1
    cfg=_auto_cfg(app);ps=universe_summary(app.csv_dir)
    L=['<b>🔥 BOOT v2.3.3 FULL POWER + DYNAMIC PRODUCTS</b>','',f"Fast pass: <b>{html.escape(str(st.get('duration_seconds') or '-'))}s</b>",f"Hot interval target: <b>{html.escape(str(cfg.get('fast_market_interval_seconds','5')))}s</b>",f"V2 cycles: <b>{counts.get('V2_CYCLE',0)}</b>",f"V3 cycles: <b>{counts.get('V3_CYCLE',0)}</b>",f"Cross-DEX shadow: <b>{counts.get('CROSS_DEX_V2',0)}</b>",f"Products tracked: <b>{ps.get('total',0)}</b> | AUTO-approved: <b>{ps.get('trade',0)}</b>",'', '<i>V2/V3 single-router cycles may auto-execute only after product policy + wallet simulation + final eth_call. Cross-DEX remains shadow-only until an atomic multi-router executor is deployed.</i>']
    return '\n'.join(L)

def products_page(app,chat_id):
    require_user(app.csv_dir,chat_id,active=False)
    rows=product_rows(app.csv_dir);ps=universe_summary(app.csv_dir)
    L=['<b>🧺 BOOT AUTO PRODUCT UNIVERSE</b>','',f"Tracked: <b>{ps.get('total',0)}</b> | scan-enabled: <b>{ps.get('scan',0)}</b> | AUTO-approved: <b>{ps.get('trade',0)}</b>",f"L1 Core: <b>{ps.get('L1',0)}</b> | L2 Liquid: <b>{ps.get('L2',0)}</b> | L3 Strict/Shadow: <b>{ps.get('L3',0)}</b> | L4 Quarantine: <b>{ps.get('L4',0)}</b>",'']
    if not rows:
        return '\n'.join(L+['Universe is waiting for the next background discovery refresh.'])
    by_chain={}
    for r in rows:by_chain.setdefault(str(r.get('chain_slug') or r.get('chain_id') or '?').upper(),[]).append(r)
    for slug,rr in by_chain.items():
        L.append(f'<b>{html.escape(slug)}</b>')
        rr.sort(key=lambda r:(int(float(r.get('risk_level') or 9)),0 if _is_on(r.get('auto_trade'),False) else 1,-int(float(r.get('pool_count') or 0))))
        shown=0
        for r in rr:
            if shown>=12:break
            risk=int(float(r.get('risk_level') or 9));mark='🟢' if risk<=2 and _is_on(r.get('auto_trade'),False) else '🟡' if risk==3 else '🔴'
            mode='AUTO' if _is_on(r.get('auto_trade'),False) else 'SHADOW' if _is_on(r.get('auto_scan'),False) else 'BLOCK'
            sym=html.escape(str(r.get('symbol') or _short(r.get('address') or '')))
            L.append(f"{mark} L{risk} <b>{sym}</b> {html.escape(mode)} | pools={html.escape(str(r.get('pool_count') or '0'))} | {html.escape(str(r.get('category') or ''))}")
            shown+=1
        L.append('')
    L += ['<i>L1 = wrapped/stable core; L2 = configured or established liquid products; L3 = stricter dynamic products/new-token shadow; L4 = temporary execution quarantine. Exact quote, price-impact, wallet simulation, gas-profit and final eth_call checks still apply to every AUTO trade.</i>']
    return '\n'.join(L)

def trading_page(app, chat_id):
    u=require_user(app.csv_dir,chat_id,active=False);store=MultiWalletStore(app.data_dir,app.csv_dir);meta=store.get_meta(chat_id) if store.has_wallet(chat_id) else None
    enabled=user_bool(app.csv_dir,chat_id,0,'live_trading_enabled',False);maxbuy=user_setting(app.csv_dir,chat_id,0,'max_native_input_per_trade',_live_cfg(app).get('max_native_input_per_trade','0.05'));slip=user_setting(app.csv_dir,chat_id,0,'slippage_bps',_live_cfg(app).get('slippage_bps','500'));gasbid=user_setting(app.csv_dir,chat_id,0,'gas_bid_multiplier',_live_cfg(app).get('gas_bid_multiplier','1.25'))
    platform_live=_is_on(_live_cfg(app).get('trading_enabled','false'),False)
    L=['<b>💱 MY LIVE TRADING — v2.3</b>','',f"Account: <b>{html.escape((u.get('status') or '').upper())}</b>",f"Platform LIVE gate: <b>{'ON' if platform_live else 'OFF'}</b>",f"My live switch: <b>{'🟢 ON' if enabled else '🔴 OFF'}</b>",f"Active wallet: <b>{html.escape(meta.get('label') or meta.get('wallet_id')) if meta else 'NOT CONFIGURED'}</b>"]
    if meta:L.append(f"Address: <code>{html.escape(_short(meta.get('address')))}</code>")
    L += [f"Max BUY: <b>{html.escape(str(maxbuy))}</b> native",f"Slippage: <b>{html.escape(str(slip))} bps</b>",f"Gas bid: <b>{html.escape(str(gasbid))}x</b>",'','<code>/balance bsc</code>','<code>/quote bsc 0xTOKEN 0.01</code>','<code>/buy bsc 0xTOKEN 0.01 CONFIRM</code>','<code>/sell bsc 0xTOKEN 50% CONFIRM</code>','<code>/setgasbid 1.25</code>','<code>/transfer bsc native 0xTO 0.001 CONFIRM</code>','<code>/live on CONFIRM</code>','<code>/live off</code>']
    return '\n'.join(L)

def _set_operator_value(app,chat_id,key,value):
    if not _telegram_write_on(app):raise ValueError('Telegram setting changes are disabled in operator_settings.csv')
    cfg=_copy_cfg(app); old=cfg.get(key,'')
    set_scoped_default(app.csv_dir/'copy_settings.csv',key,value)
    audit(app.csv_dir,chat_id,'SET_COPY_SETTING',key,old,str(value))

def _handle_pending_input(app,chat_id,text):
    pending=_PENDING_INPUT.get(str(chat_id))
    if not pending:return False
    if text.lower() in {'cancel','/cancel'}:
        _PENDING_INPUT.pop(str(chat_id),None);_send(app,chat_id,'Input cancelled.',control_keyboard(app));return True
    spec=_PARAM_SPECS.get(pending)
    if not spec:
        _PENDING_INPUT.pop(str(chat_id),None);return False
    label_text,lo,hi,key=spec
    try:
        value=parse_float(text,minimum=lo,maximum=hi,name=label_text)
        out=f'{value:g}'
        _set_operator_value(app,chat_id,key,out)
        _PENDING_INPUT.pop(str(chat_id),None)
        _send(app,chat_id,f"✅ {html.escape(label_text)} set to <b>{html.escape(out)}</b>.",control_keyboard(app))
    except ValueError as e:
        _send(app,chat_id,f"❌ {html.escape(str(e))}\nSend another value or <code>/cancel</code>.")
    return True

def _command_value(text):
    parts=text.split(maxsplit=1);return parts[1].strip() if len(parts)>1 else ''

def status_page(app):
    cfg=_copy_cfg(app); mode=(cfg.get('recommendation_mode','SHADOW') or 'SHADOW').upper()
    L=['<b>📡 MULTI-CHAIN STATUS</b>','',f"Learning engine: <b>{'ON' if _operator_on(app) else 'PAUSED'}</b>",f"Recommendation mode: <b>{html.escape(mode)}</b>",'']
    for c in _ctxs(app):
        last=get_state(c.conn,'last_scanned_block','-'); tx=c.conn.execute('SELECT COUNT(*) n FROM transactions').fetchone()['n']; w=c.conn.execute('SELECT COUNT(*) n FROM wallet_scores').fetchone()['n']; st=c.conn.execute('SELECT COUNT(*) n FROM strategy_patterns').fetchone()['n']
        L.append(f"<b>{html.escape(c.config.name)}</b>: block <code>{html.escape(str(last))}</code>, tx {tx:,}, wallets {w:,}, strategies {st:,}")
    q=queue_summary(app.csv_dir); _users=__import__('learnerbot.user_registry',fromlist=['all_users']).all_users(app.csv_dir); _wallets=_file_rows(app.csv_dir/'auto'/'user_wallets.csv')
    L += ['', 'CSV hot reload: <b>ON</b> (every learning cycle)',f"Telegram automatic pushes: <b>{'ON' if _master_updates_on(app) else 'OFF'}</b>",f"Execution queue pending: <b>{q['pending']}</b>",f"Registered Telegram users: <b>{len(_users)}</b>",f"Enabled user wallets: <b>{sum(1 for r in _wallets if _is_on(r.get('enabled'),True))}</b>",f"Platform LIVE gate: <b>{'ON' if _is_on(_live_cfg(app).get('trading_enabled','false'),False) else 'OFF'}</b>",f"Platform AUTO gate: <b>{'ON' if _is_on(_auto_cfg(app).get('auto_trading_enabled','false'),False) else 'OFF'}</b>"]
    return '\n'.join(L)
def _auth(app,chat_id):
    ensure_master_seed(app)
    return get_user(app.csv_dir,chat_id) is not None

def _master(app,chat_id):
    ensure_master_seed(app); return is_master(app.csv_dir,chat_id)

def _require_master(app,chat_id):
    if not _master(app,chat_id): raise ValueError('Master/admin permission required for this command')
def _send(app,chat_id,text,kb=None):send_message(app.telegram_bot_token,str(chat_id),text,parse_mode='HTML',reply_markup=kb)
def handle_update(app,u):
    cb=u.get('callback_query')
    if cb:
        chat_id=((cb.get('message') or {}).get('chat') or {}).get('id'); cqid=cb.get('id'); data=cb.get('data') or ''
        if not _auth(app,chat_id):
            if cqid:answer_callback_query(app.telegram_bot_token,cqid,'Not authorised.')
            return
        _master_pages={'menu:control','menu:alerts','menu:copy20','menu:signals','menu:wallets','menu:profit','menu:behaviours','menu:rankings','menu:strategies','menu:queue','menu:report'}
        if (data in _master_pages or data.startswith('control:') or data.startswith('alerts:') or data.startswith('strategy:')) and not _master(app,chat_id):
            if cqid:answer_callback_query(app.telegram_bot_token,cqid,'MASTER only')
            return
        if cqid:answer_callback_query(app.telegram_bot_token,cqid)
        try:
            if data=='menu:home':_send(app,chat_id,home_text(),menu_keyboard(app,chat_id))
            elif data=='menu:control':_send(app,chat_id,control_page(app),control_keyboard(app))
            elif data=='menu:queue':_send(app,chat_id,queue_page(app),back_keyboard())
            elif data=='menu:wallet':_send(app,chat_id,wallet_page(app,chat_id),back_keyboard())
            elif data=='menu:auto':_send(app,chat_id,auto_page(app,chat_id),back_keyboard())
            elif data=='menu:opportunities':_send(app,chat_id,opportunities_page(app,chat_id),back_keyboard())
            elif data=='menu:products':_send(app,chat_id,products_page(app,chat_id),back_keyboard())
            elif data=='menu:power':_send(app,chat_id,power_page(app,chat_id),back_keyboard())
            elif data=='menu:trading':_send(app,chat_id,trading_page(app,chat_id),back_keyboard())
            elif data=='menu:alerts':_send(app,chat_id,alerts_page(app),alerts_keyboard(app))
            elif data.startswith('alerts:master:'):
                on=data.endswith(':on');_set_master_updates(app,on);_send(app,chat_id,alerts_page(app),alerts_keyboard(app))
            elif data.startswith('alerts:toggle:'):
                key=data.split(':',2)[2];_toggle_alert_category(app,key);_send(app,chat_id,alerts_page(app),alerts_keyboard(app))
            elif data.startswith('alerts:categories:'):
                on=data.endswith(':on')
                for key in _ALERT_DESCRIPTIONS:_set_alert_category(app,key,on)
                _send(app,chat_id,alerts_page(app),alerts_keyboard(app))
            elif data.startswith('control:engine:'):
                if not _telegram_write_on(app):raise ValueError('Telegram setting changes are disabled.')
                on=data.endswith(':on'); old=app.operator_settings().get('engine_enabled','true')
                set_kv(app.csv_dir/'operator_settings.csv','engine_enabled','true' if on else 'false','Master scanner/learning engine switch controlled from Telegram')
                audit(app.csv_dir,chat_id,'ENGINE','engine_enabled',old,'true' if on else 'false')
                _send(app,chat_id,control_page(app),control_keyboard(app))
            elif data.startswith('control:mode:'):
                mode=data.rsplit(':',1)[1].upper()
                if mode not in {'SHADOW','ARMED'}:raise ValueError('Unsupported mode')
                _set_operator_value(app,chat_id,'recommendation_mode',mode)
                _send(app,chat_id,control_page(app),control_keyboard(app))
            elif data.startswith('control:chain:'):
                if not _telegram_write_on(app):raise ValueError('Telegram setting changes are disabled.')
                _,_,cid_s,state=data.split(':',3);cid=int(cid_s);on=state=='on'
                old=next((c.enabled for c in load_chains(app,False) if c.chain_id==cid),None)
                set_chain_enabled(app.csv_dir/'chains.csv',cid,on);audit(app.csv_dir,chat_id,'CHAIN',str(cid),str(old),str(on))
                _send(app,chat_id,control_page(app),control_keyboard(app))
            elif data=='control:behaviours':
                page,kb=behaviours_control_page(app);_send(app,chat_id,page,kb)
            elif data.startswith('control:behaviour:'):
                if not _telegram_write_on(app):raise ValueError('Telegram setting changes are disabled.')
                key=data.split(':',2)[2];cfg=_copy_cfg(app);allowed=[x for x in (cfg.get('allowed_behaviours','') or '').split('|') if x]
                if key in allowed:
                    if len(allowed)<=1:raise ValueError('Keep at least one copy-eligible behaviour enabled.')
                    allowed=[x for x in allowed if x!=key]
                else:allowed.append(key)
                old=cfg.get('allowed_behaviours','');set_allowed_behaviours(app.csv_dir/'copy_settings.csv',allowed);audit(app.csv_dir,chat_id,'BEHAVIOURS','allowed_behaviours',old,'|'.join(allowed))
                page,kb=behaviours_control_page(app);_send(app,chat_id,page,kb)
            elif data.startswith('control:ask:'):
                key=data.split(':',2)[2]
                if key not in _PARAM_SPECS:raise ValueError('Unknown setting')
                label_text,lo,hi,_=_PARAM_SPECS[key];_PENDING_INPUT[str(chat_id)]=key
                _send(app,chat_id,f"Send the new value for <b>{html.escape(label_text)}</b>.\nAllowed range: <code>{lo:g}</code> to <code>{hi:g}</code>.\nSend <code>/cancel</code> to stop.")
            elif data=='menu:chains':_send(app,chat_id,chains_page(app),back_keyboard())
            elif data=='menu:copy20':_send(app,chat_id,copy20_page(app),back_keyboard())
            elif data=='menu:signals':_send(app,chat_id,signals_page(app),back_keyboard())
            elif data=='menu:wallets':_send(app,chat_id,wallets_page(app),back_keyboard())
            elif data=='menu:profit':_send(app,chat_id,profit_page(app),back_keyboard())
            elif data=='menu:behaviours':_send(app,chat_id,behaviours_page(app),back_keyboard())
            elif data=='menu:rankings':_send(app,chat_id,rankings_page(app),back_keyboard())
            elif data=='menu:strategies':
                page,kb=strategies_page(app);_send(app,chat_id,page,kb)
            elif data=='menu:help':_send(app,chat_id,help_page(),back_keyboard())
            elif data=='menu:status':_send(app,chat_id,status_page(app),back_keyboard())
            elif data=='menu:report':_send(app,chat_id,build_report_html(app),back_keyboard())
            elif data.startswith('strategy:'):
                _,slug,pid=data.split(':',2); page,kb=strategy_detail(app,slug,pid);_send(app,chat_id,page,kb)
        except Exception as e:
            _send(app,chat_id,f"❌ Control change rejected: {html.escape(str(e))}",control_keyboard(app) if data.startswith('control:') else back_keyboard())
        return
    m=u.get('message') or {}; chat_id=(m.get('chat') or {}).get('id'); text=(m.get('text') or '').strip(); cmd=text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
    if chat_id is None:return
    if not _auth(app,chat_id) and cmd not in {'/start','/menu','/join','/activate'}:return
    if _auth(app,chat_id) and _handle_pending_input(app,chat_id,text):return
    try:
        if cmd in {'/start','/menu'}:
            if not _auth(app,chat_id):_send(app,chat_id,'<b>Welcome.</b> Use <code>/join</code> for the default fee plan or <code>/activate CODE</code> if you have an activation code.')
            else:_send(app,chat_id,home_text(),menu_keyboard(app,chat_id))
        elif cmd=='/control':
            _require_master(app,chat_id);_send(app,chat_id,control_page(app),control_keyboard(app))
        elif cmd=='/queue':
            _require_master(app,chat_id);_send(app,chat_id,queue_page(app),back_keyboard())
        elif cmd=='/join':
            if get_user(app.csv_dir,chat_id):
                _send(app,chat_id,'You are already registered.',back_keyboard())
            else:
                plan=(app.operator_settings().get('default_fee_plan_id','STANDARD') or 'STANDARD').strip()
                u=join_user(app.csv_dir,chat_id,plan);fp=user_fee_plan(app.csv_dir,chat_id) or {};mode=(fp.get('activation_mode') or 'NONE').upper()
                if mode=='NONE':u=activate_user(app.csv_dir,chat_id,plan,'No activation fee required by plan')
                msg=f"✅ Registered. Status: <b>{html.escape((u.get('status') or '').upper())}</b> | plan: <b>{html.escape(plan)}</b>\nUse <code>/walletcreate CONFIRM</code> or <code>/walletimport ... CONFIRM</code>."
                if mode in {'FIXED','CODE_OR_FIXED'} and (u.get('status') or '').upper()!='ACTIVE':msg += "\nThen use <code>/payactivation bsc CONFIRM</code>."
                _send(app,chat_id,msg,back_keyboard())
        elif cmd=='/activate':
            parts=text.split(maxsplit=1)
            if len(parts)!=2:raise ValueError('Use /activate CODE')
            u=redeem_activation_code(app.csv_dir,chat_id,parts[1]);_send(app,chat_id,f"✅ Account activated. Plan: <b>{html.escape(u.get('fee_plan_id') or '')}</b>.",menu_keyboard(app,chat_id))
        elif cmd=='/wallet':
            _send(app,chat_id,wallet_page(app,chat_id),back_keyboard())
        elif cmd=='/walletcreate':
            require_user(app.csv_dir,chat_id,active=False);parts=text.split();label='Wallet'
            if len(parts)==2 and parts[1].upper()=='CONFIRM':pass
            elif len(parts)==3 and parts[2].upper()=='CONFIRM':label=parts[1]
            else:raise ValueError('Use /walletcreate CONFIRM or /walletcreate LABEL CONFIRM')
            r=MultiWalletStore(app.data_dir,app.csv_dir).create(chat_id,label);audit(app.csv_dir,chat_id,'WALLET_CREATE',r['wallet_id'],'',r['address'])
            _send(app,chat_id,f"✅ Wallet created.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>\nActive: <b>{r['active']}</b>",back_keyboard())
        elif cmd=='/walletimport':
            require_user(app.csv_dir,chat_id,active=False);parts=text.split()
            if (m.get('chat') or {}).get('type')!='private':raise ValueError('Private-key import is allowed only in a private Telegram chat')
            if len(parts)==3 and parts[2].upper()=='CONFIRM':label='Imported';key=parts[1]
            elif len(parts)==4 and parts[3].upper()=='CONFIRM':label=parts[1];key=parts[2]
            else:raise ValueError('Use /walletimport 0xPRIVATEKEY CONFIRM or /walletimport LABEL 0xPRIVATEKEY CONFIRM')
            mid=m.get('message_id')
            if not mid or not delete_message(app.telegram_bot_token,chat_id,mid):raise ValueError('Telegram did not confirm deletion; private key was NOT saved')
            r=MultiWalletStore(app.data_dir,app.csv_dir).save_private_key(chat_id,key,label=label,source='telegram-import');audit(app.csv_dir,chat_id,'WALLET_IMPORT',r['wallet_id'],'',r['address'],'incoming secret deleted before persistence')
            _send(app,chat_id,f"✅ Wallet imported. Secret message deleted.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>",back_keyboard())
        elif cmd=='/walletuse':
            require_user(app.csv_dir,chat_id,active=False);parts=text.split()
            if len(parts)!=2:raise ValueError('Use /walletuse WALLET_ID')
            r=MultiWalletStore(app.data_dir,app.csv_dir).set_active(chat_id,parts[1]);_send(app,chat_id,f"✅ Active wallet: <b>{html.escape(r.get('label') or '')}</b> <code>{html.escape(r.get('wallet_id') or '')}</code>\n{html.escape(r.get('address') or '')}",back_keyboard())
        elif cmd in {'/walletremove','/walletforget'}:
            require_user(app.csv_dir,chat_id,active=False);parts=text.split()
            if len(parts)!=3 or parts[2].upper()!='CONFIRM':raise ValueError('Use /walletremove WALLET_ID CONFIRM')
            set_user_setting(app.csv_dir,chat_id,'auto_trading_enabled','false',description='User automatic route execution switch');set_user_setting(app.csv_dir,chat_id,'live_trading_enabled','false',description='User live signing switch')
            MultiWalletStore(app.data_dir,app.csv_dir).forget(chat_id,parts[1]);_send(app,chat_id,'✅ Wallet removed from your encrypted server store. Your LIVE and AUTOTRADE switches were turned off.',back_keyboard())
        elif cmd=='/assets':
            require_user(app.csv_dir,chat_id,active=False);parts=text.split();slugs=[parts[1].lower()] if len(parts)>=2 and parts[1].lower()!='all' else [c.slug for c in load_chains(app,enabled_only=True)];L=['<b>💼 MY ACTIVE WALLET ASSETS</b>','']
            for slug in slugs:
                require_user(app.csv_dir,chat_id,active=False,chain_slug=slug)
                a=wallet_assets(app,slug,discover_recent=True,telegram_id=chat_id);L += [f"<b>{html.escape(a['name'])}</b>",f"Address: <code>{html.escape(a['wallet'])}</code>",f"{html.escape(a['native_symbol'])}: <b>{a['native_balance']:.8f}</b>"]
                for tok in a['tokens'][:30]:L.append(f"• {html.escape(tok['symbol'])}: <b>{tok['balance']:f}</b> <code>{html.escape(_short(tok['address']))}</code>")
                if not a['tokens']:L.append('• No non-zero tracked/recent token balances found.')
                L.append('')
            _send(app,chat_id,'\n'.join(L),back_keyboard())
        elif cmd=='/transfer':
            parts=text.split()
            if len(parts)!=6 or parts[5].upper()!='CONFIRM':raise ValueError('Use /transfer bsc native 0xTO 0.001 CONFIRM OR /transfer bsc 0xTOKEN 0xTO 50% CONFIRM')
            u=require_user(app.csv_dir,chat_id,active=True,chain_slug=parts[1])
            if not _is_on(u.get('can_transfer'),True):raise ValueError('Transfers are disabled for your account')
            t=LiveTrader(app,parts[1],telegram_id=chat_id)
            if parts[2].lower()=='native':r=t.transfer_native(parts[3],parts[4],'CONFIRM');desc=f"{r['amount']} {t.chain.native_symbol}"
            else:r=t.transfer_token(parts[2],parts[3],parts[4],'CONFIRM');desc=f"{r['amount']} {r['symbol']}"
            audit(app.csv_dir,chat_id,'TRANSFER',parts[1],'',r['tx_hash'],desc);_send(app,chat_id,f"📤 Transfer broadcast: <b>{html.escape(desc)}</b>\nTo: <code>{html.escape(r['to'])}</code>\nTX: <code>{html.escape(r['tx_hash'])}</code>",back_keyboard())
        elif cmd=='/fees':
            u=require_user(app.csv_dir,chat_id,active=False);plan=user_fee_plan(app.csv_dir,chat_id) or {}
            _send(app,chat_id,f"<b>💳 MY FEE PLAN</b>\nPlan: <b>{html.escape(u.get('fee_plan_id') or '-')}</b>\nActivation mode: <b>{html.escape(plan.get('activation_mode') or 'NONE')}</b>\nFixed native activation fee: <b>{html.escape(plan.get('activation_fee_native') or '0')}</b>\nProfit share: <b>{float(plan.get('profit_share_bps') or 0)/100:.2f}%</b>\nStatus: <b>{html.escape((u.get('status') or '').upper())}</b>",back_keyboard())
        elif cmd=='/payactivation':
            parts=text.split()
            if len(parts)!=3 or parts[2].upper()!='CONFIRM':raise ValueError('Use /payactivation bsc CONFIRM')
            u=require_user(app.csv_dir,chat_id,active=False,chain_slug=parts[1])
            if (u.get('status') or '').upper()=='ACTIVE':raise ValueError('Account is already active')
            plan=user_fee_plan(app.csv_dir,chat_id) or {};mode=(plan.get('activation_mode') or 'NONE').upper()
            if mode not in {'FIXED','CODE_OR_FIXED'}:raise ValueError('Your plan is not configured for fixed-fee activation')
            amount,master,chain=fixed_activation_fee(app.csv_dir,chat_id,parts[1].lower(),app)
            if amount<=0:raise ValueError('Activation fee is not configured for this chain')
            if not master:raise ValueError('Master fee wallet is not configured for this chain')
            trader=LiveTrader(app,parts[1],telegram_id=chat_id);r=trader.transfer_native(master,amount,'CONFIRM')
            try:receipt=trader.w3.eth.wait_for_transaction_receipt(r['tx_hash'],timeout=180,poll_latency=2)
            except Exception as exc:raise ValueError(f"Activation payment broadcast {r['tx_hash']} but confirmation timed out; account remains pending until verified") from exc
            if int(receipt.status)!=1:raise ValueError('Activation payment transaction failed; account remains pending')
            mark_activation_paid(app.csv_dir,chat_id,u.get('fee_plan_id') or 'STANDARD',chain.chain_id,amount,master,r['tx_hash'])
            _send(app,chat_id,f"✅ Activation fee confirmed and account activated.\nAmount: <b>{amount} {html.escape(chain.native_symbol)}</b>\nMaster: <code>{html.escape(master)}</code>\nTX: <code>{html.escape(r['tx_hash'])}</code>",back_keyboard())
        elif cmd=='/auto':
            _send(app,chat_id,auto_page(app,chat_id),back_keyboard())
        elif cmd=='/opportunities':
            _send(app,chat_id,opportunities_page(app,chat_id),back_keyboard())
        elif cmd=='/power':
            _send(app,chat_id,power_page(app,chat_id),back_keyboard())
        elif cmd=='/products':
            _send(app,chat_id,products_page(app,chat_id),back_keyboard())
        elif cmd=='/autoprep':
            parts=text.split()
            if len(parts)!=4 or parts[3].upper()!='CONFIRM':raise ValueError('Use /autoprep bsc 0.01 CONFIRM')
            require_user(app.csv_dir,chat_id,active=True,chain_slug=parts[1])
            maxv=float(user_setting(app.csv_dir,chat_id,0,'max_auto_input_base',_auto_cfg(app).get('max_auto_input_base','0.05')));amount=parse_float(parts[2],minimum=0.000001,maximum=maxv,name='auto capital')
            r=LiveTrader(app,parts[1],telegram_id=chat_id).prepare_auto(amount,'CONFIRM');audit(app.csv_dir,chat_id,'AUTO_PREP',parts[1],'',str(amount),'wrapped capital + bounded router allowance')
            _send(app,chat_id,f"✅ Auto capital prepared: <b>{amount:g}</b> wrapped-native. Wrapped balance: <b>{r['wrapped_balance']:f}</b>.",back_keyboard())
        elif cmd=='/autotrade':
            u=require_user(app.csv_dir,chat_id,active=True);parts=text.split()
            if len(parts)<2 or parts[1].lower() not in {'on','off'}:raise ValueError('Use /autotrade on CONFIRM or /autotrade off')
            on=parts[1].lower()=='on'
            if on:
                if len(parts)<3 or parts[2].upper()!='CONFIRM':raise ValueError('Use /autotrade on CONFIRM')
                if not _is_on(u.get('can_auto_trade'),True):raise ValueError('Automatic trading is disabled for your account')
                if not MultiWalletStore(app.data_dir,app.csv_dir).has_wallet(chat_id):raise ValueError('Create/import a wallet first')
                if not user_bool(app.csv_dir,chat_id,0,'live_trading_enabled',False):raise ValueError('Enable /live on CONFIRM first')
                if str(user_setting(app.csv_dir,chat_id,0,'recommendation_mode','SHADOW')).upper()!='ARMED':raise ValueError('Set /mode armed first')
            old=user_setting(app.csv_dir,chat_id,0,'auto_trading_enabled','false');set_user_setting(app.csv_dir,chat_id,'auto_trading_enabled','true' if on else 'false',description='User automatic route execution switch');audit(app.csv_dir,chat_id,'USER_AUTO','auto_trading_enabled',old,str(on))
            _send(app,chat_id,'⚠️ <b>MY AUTOTRADE ENABLED.</b> Platform master gate must also be ON.' if on else '✅ My automatic trading disabled.',back_keyboard())
        elif cmd=='/setautosize':
            require_user(app.csv_dir,chat_id,active=False);raw=_command_value(text);v=parse_float(raw,minimum=0.000001,maximum=float(_auto_cfg(app).get('max_auto_input_base','0.05')),name='automatic route input');old=user_setting(app.csv_dir,chat_id,0,'auto_input_base',_auto_cfg(app).get('auto_input_base','0.005'));set_user_setting(app.csv_dir,chat_id,'auto_input_base',f'{v:g}',description='User automatic wrapped-native route input');audit(app.csv_dir,chat_id,'USER_AUTO_SETTING','auto_input_base',old,f'{v:g}');_send(app,chat_id,f'✅ My automatic route input set to <b>{v:g}</b>.',back_keyboard())
        elif cmd=='/setautoprofit':
            require_user(app.csv_dir,chat_id,active=False);raw=_command_value(text);v=parse_float(raw,minimum=0.0,maximum=100,name='minimum retained automatic net profit');old=user_setting(app.csv_dir,chat_id,0,'min_net_profit_base',_auto_cfg(app).get('min_net_profit_base','0.0002'));set_user_setting(app.csv_dir,chat_id,'min_net_profit_base',f'{v:g}',description='Minimum user-retained route net after cycle gas and fee reserve');audit(app.csv_dir,chat_id,'USER_AUTO_SETTING','min_net_profit_base',old,f'{v:g}');_send(app,chat_id,f'✅ My minimum retained automatic net profit set to <b>{v:g}</b>.',back_keyboard())
        elif cmd=='/trading':
            _send(app,chat_id,trading_page(app,chat_id),back_keyboard())
        elif cmd=='/live':
            u=require_user(app.csv_dir,chat_id,active=True);parts=text.split()
            if len(parts)<2 or parts[1].lower() not in {'on','off'}:raise ValueError('Use /live on CONFIRM or /live off')
            on=parts[1].lower()=='on'
            if on:
                if len(parts)<3 or parts[2].upper()!='CONFIRM':raise ValueError('Use /live on CONFIRM')
                if not _is_on(u.get('can_manual_trade'),True):raise ValueError('Live trading is disabled for your account')
                if not MultiWalletStore(app.data_dir,app.csv_dir).has_wallet(chat_id):raise ValueError('Create/import a wallet first')
            old=user_setting(app.csv_dir,chat_id,0,'live_trading_enabled','false');set_user_setting(app.csv_dir,chat_id,'live_trading_enabled','true' if on else 'false',description='User live signing switch');audit(app.csv_dir,chat_id,'USER_LIVE','live_trading_enabled',old,str(on));_send(app,chat_id,'⚠️ <b>MY LIVE TRADING ENABLED.</b>' if on else '✅ My live trading disabled.',back_keyboard())
        elif cmd=='/balance':
            parts=text.split()
            if len(parts)<2:raise ValueError('Use /balance bsc [0xTOKEN]')
            require_user(app.csv_dir,chat_id,active=False,chain_slug=parts[1])
            t=LiveTrader(app,parts[1],telegram_id=chat_id);st=t.status();L=[f"<b>💼 {html.escape(st['name'])} — MY ACTIVE WALLET</b>",f"Address: <code>{html.escape(st['wallet'])}</code>",f"Native: <b>{st['native_balance']:.8f} {html.escape(st['native_symbol'])}</b>"]
            if len(parts)>=3:_,_,_,sym,_,bal=t.token_balance(parts[2]);L.append(f"{html.escape(sym)}: <b>{bal:f}</b>")
            _send(app,chat_id,'\n'.join(L),back_keyboard())
        elif cmd=='/quote':
            parts=text.split()
            if len(parts)!=4:raise ValueError('Use /quote bsc 0xTOKEN 0.01')
            require_user(app.csv_dir,chat_id,active=False,chain_slug=parts[1])
            q=LiveTrader(app,parts[1],telegram_id=chat_id).quote_buy(parts[2],parts[3]);_send(app,chat_id,f"<b>BUY QUOTE — {html.escape(q.chain_slug.upper())}</b>\nInput: <b>{html.escape(q.amount_in_human)}</b> native\nExpected: <b>{html.escape(q.expected_out_human)} {html.escape(q.token_symbol)}</b>\nMinimum: <b>{html.escape(q.minimum_out_human)} {html.escape(q.token_symbol)}</b>\nSlippage: {q.slippage_bps/100:.2f}%",back_keyboard())
        elif cmd=='/buy':
            parts=text.split()
            if len(parts)!=5:raise ValueError('Use /buy bsc 0xTOKEN 0.01 CONFIRM')
            u=require_user(app.csv_dir,chat_id,active=True,chain_slug=parts[1])
            if not _is_on(u.get('can_manual_trade'),True):raise ValueError('Manual trading is disabled for your account')
            r=LiveTrader(app,parts[1],telegram_id=chat_id).buy(parts[2],parts[3],parts[4]);q=r['quote'];audit(app.csv_dir,chat_id,'LIVE_BUY',q.token,'',r['tx_hash'],q.amount_in_human);_send(app,chat_id,f'🚀 <b>BUY BROADCAST</b>\nInput: {html.escape(q.amount_in_human)} native\nExpected: {html.escape(q.expected_out_human)} {html.escape(q.token_symbol)}\nTX: <code>{html.escape(r["tx_hash"])}</code>',back_keyboard())
        elif cmd=='/sell':
            parts=text.split()
            if len(parts)!=5:raise ValueError('Use /sell bsc 0xTOKEN 50% CONFIRM')
            u=require_user(app.csv_dir,chat_id,active=True,chain_slug=parts[1])
            if not _is_on(u.get('can_manual_trade'),True):raise ValueError('Manual trading is disabled for your account')
            r=LiveTrader(app,parts[1],telegram_id=chat_id).sell(parts[2],parts[3],parts[4]);q=r['quote'];audit(app.csv_dir,chat_id,'LIVE_SELL',q.token,'',r['tx_hash'],q.amount_in_human);_send(app,chat_id,f'💸 <b>SELL BROADCAST</b>\nSold: {html.escape(q.amount_in_human)} {html.escape(q.token_symbol)}\nExpected: {html.escape(q.expected_out_human)} native\nTX: <code>{html.escape(r["tx_hash"])}</code>',back_keyboard())
        elif cmd=='/settrademax':
            require_user(app.csv_dir,chat_id,active=False);raw=_command_value(text);v=parse_float(raw,minimum=0.000001,maximum=100,name='maximum live BUY input');old=user_setting(app.csv_dir,chat_id,0,'max_native_input_per_trade',_live_cfg(app).get('max_native_input_per_trade','0.05'));set_user_setting(app.csv_dir,chat_id,'max_native_input_per_trade',f'{v:g}',description='User maximum native manual BUY');audit(app.csv_dir,chat_id,'USER_LIVE_SETTING','max_native_input_per_trade',old,f'{v:g}');_send(app,chat_id,f"✅ My max BUY input set to <b>{v:g}</b>.",back_keyboard())
        elif cmd=='/setslippage':
            require_user(app.csv_dir,chat_id,active=False);raw=_command_value(text);v=parse_float(raw,minimum=1,maximum=5000,name='slippage bps');old=user_setting(app.csv_dir,chat_id,0,'slippage_bps',_live_cfg(app).get('slippage_bps','500'));set_user_setting(app.csv_dir,chat_id,'slippage_bps',f'{v:g}',description='User manual-trade slippage basis points');audit(app.csv_dir,chat_id,'USER_LIVE_SETTING','slippage_bps',old,f'{v:g}');_send(app,chat_id,f"✅ My slippage set to <b>{v:g} bps</b> ({v/100:.2f}%).",back_keyboard())
        elif cmd=='/setgasbid':
            require_user(app.csv_dir,chat_id,active=False);raw=_command_value(text);v=parse_float(raw,minimum=1.0,maximum=3.0,name='gas bid multiplier');old=user_setting(app.csv_dir,chat_id,0,'gas_bid_multiplier',_live_cfg(app).get('gas_bid_multiplier','1.25'));set_user_setting(app.csv_dir,chat_id,'gas_bid_multiplier',f'{v:g}',description='User transaction fee-price multiplier; 1.0=node suggestion');audit(app.csv_dir,chat_id,'USER_LIVE_SETTING','gas_bid_multiplier',old,f'{v:g}');_send(app,chat_id,f"✅ My gas bid multiplier set to <b>{v:g}x</b>. Higher bids reduce expected net profit and are included in simulation.",back_keyboard())
        elif cmd=='/tx':
            parts=text.split()
            if len(parts)!=3:raise ValueError('Use /tx bsc 0xTRANSACTION_HASH')
            require_user(app.csv_dir,chat_id,active=False,chain_slug=parts[1])
            r=LiveTrader(app,parts[1],telegram_id=chat_id).tx_status(parts[2]);_send(app,chat_id,f"<b>TX STATUS</b>\n{html.escape(r['status'])}"+(f"\nBlock: {r.get('block')}" if r.get('block') else '')+f'\n<a href="{html.escape(r["explorer"])}">Open transaction</a>',back_keyboard())
        elif cmd=='/engine':
            _require_master(app,chat_id);v=_command_value(text).lower()
            if v not in {'on','off'}:raise ValueError('Use /engine on or /engine off')
            if not _telegram_write_on(app):raise ValueError('Telegram setting changes are disabled.')
            old=app.operator_settings().get('engine_enabled','true');set_kv(app.csv_dir/'operator_settings.csv','engine_enabled','true' if v=='on' else 'false','Master scanner/learning engine switch controlled from Telegram');audit(app.csv_dir,chat_id,'ENGINE','engine_enabled',old,v)
            _send(app,chat_id,control_page(app),control_keyboard(app))
        elif cmd=='/mode':
            require_user(app.csv_dir,chat_id,active=False);v=_command_value(text).upper()
            if v not in {'SHADOW','ARMED'}:raise ValueError('Use /mode shadow or /mode armed')
            old=user_setting(app.csv_dir,chat_id,0,'recommendation_mode','SHADOW');set_user_setting(app.csv_dir,chat_id,'recommendation_mode',v,description='User automatic execution mode');audit(app.csv_dir,chat_id,'USER_MODE','recommendation_mode',old,v);_send(app,chat_id,f'✅ My mode set to <b>{html.escape(v)}</b>.',back_keyboard())
        elif cmd in {'/setmax','/setprofit','/setcopy','/setedge','/setage','/setcanary','/setscore'}:
            _require_master(app,chat_id);mapping={'/setmax':'max_copy_input_base','/setprofit':'min_conservative_profit_base','/setcopy':'copy_size_pct_of_source','/setedge':'copy_edge_capture_pct','/setage':'max_signal_age_seconds','/setcanary':'canary_input_base','/setscore':'min_copy_score'}
            key=mapping[cmd];raw=_command_value(text)
            if not raw:
                _PENDING_INPUT[str(chat_id)]=key;label_text,lo,hi,_=_PARAM_SPECS[key];_send(app,chat_id,f"Send {html.escape(label_text)} ({lo:g}–{hi:g}), or /cancel.");return
            label_text,lo,hi,_=_PARAM_SPECS[key];v=parse_float(raw,minimum=lo,maximum=hi,name=label_text);_set_operator_value(app,chat_id,key,f'{v:g}');_send(app,chat_id,control_page(app),control_keyboard(app))
        elif cmd=='/platformlive':
            _require_master(app,chat_id);parts=text.split()
            if len(parts)<2 or parts[1].lower() not in {'on','off'}:raise ValueError('Use /platformlive on CONFIRM or /platformlive off')
            on=parts[1].lower()=='on'
            if on and (len(parts)<3 or parts[2].upper()!='CONFIRM'):raise ValueError('Use /platformlive on CONFIRM')
            old=_live_cfg(app).get('trading_enabled','false');set_scoped_default(app.csv_dir/'live_trading_settings.csv','trading_enabled','true' if on else 'false','MASTER platform live-signing emergency gate');audit(app.csv_dir,chat_id,'PLATFORM_LIVE','trading_enabled',old,str(on));_send(app,chat_id,'⚠️ Platform LIVE signing gate ON.' if on else '✅ Platform LIVE signing gate OFF.',back_keyboard())
        elif cmd=='/platformauto':
            _require_master(app,chat_id);parts=text.split()
            if len(parts)<2 or parts[1].lower() not in {'on','off'}:raise ValueError('Use /platformauto on CONFIRM or /platformauto off')
            on=parts[1].lower()=='on'
            if on and (len(parts)<3 or parts[2].upper()!='CONFIRM'):raise ValueError('Use /platformauto on CONFIRM')
            old=_auto_cfg(app).get('auto_trading_enabled','false');set_scoped_default(app.csv_dir/'auto_trading_settings.csv','auto_trading_enabled','true' if on else 'false','MASTER platform automatic execution gate');audit(app.csv_dir,chat_id,'PLATFORM_AUTO','auto_trading_enabled',old,str(on));_send(app,chat_id,'⚠️ Platform automatic execution gate ON.' if on else '✅ Platform automatic execution gate OFF.',back_keyboard())
        elif cmd=='/adminusers':
            _require_master(app,chat_id);rows=__import__('learnerbot.user_registry',fromlist=['all_users']).all_users(app.csv_dir);L=['<b>👥 PLATFORM USERS</b>','']
            for r in rows[:100]:L.append(f"• <code>{html.escape(r.get('telegram_id') or '')}</code> {html.escape((r.get('role') or 'USER').upper())} {html.escape((r.get('status') or '').upper())} plan={html.escape(r.get('fee_plan_id') or '-')}")
            _send(app,chat_id,'\n'.join(L),back_keyboard())
        elif cmd=='/admincode':
            _require_master(app,chat_id);parts=text.split()
            if len(parts)<2:raise ValueError('Use /admincode PLAN [MAX_USES] [DAYS]')
            plan=parts[1]
            if not fee_plan(app.csv_dir,plan):raise ValueError('Fee plan is missing or disabled in fee_plans.csv')
            maxuses=int(parts[2]) if len(parts)>=3 else 1;days=int(parts[3]) if len(parts)>=4 else 30;expires=int(time.time())+days*86400 if days>0 else 0;code=create_activation_code(app.csv_dir,plan,maxuses,expires,'Created from MASTER Telegram')
            _send(app,chat_id,f"✅ Activation code created for <b>{html.escape(plan)}</b> ({maxuses} uses, {days} days):\n<code>{html.escape(code)}</code>\nOnly the SHA-256 hash is stored in CSV.",back_keyboard())
        elif cmd=='/alerts':
            _require_master(app,chat_id);_send(app,chat_id,alerts_page(app),alerts_keyboard(app))
        elif cmd=='/chains':_send(app,chat_id,chains_page(app),back_keyboard())
        elif cmd=='/copy20':
            _require_master(app,chat_id);_send(app,chat_id,copy20_page(app),back_keyboard())
        elif cmd=='/signals':
            _require_master(app,chat_id);_send(app,chat_id,signals_page(app),back_keyboard())
        elif cmd=='/wallets':
            _require_master(app,chat_id);_send(app,chat_id,wallets_page(app),back_keyboard())
        elif cmd=='/profit':
            _require_master(app,chat_id);_send(app,chat_id,profit_page(app),back_keyboard())
        elif cmd=='/behaviours':
            _require_master(app,chat_id);_send(app,chat_id,behaviours_page(app),back_keyboard())
        elif cmd=='/rankings':
            _require_master(app,chat_id);_send(app,chat_id,rankings_page(app),back_keyboard())
        elif cmd=='/strategies':
            _require_master(app,chat_id);page,kb=strategies_page(app);_send(app,chat_id,page,kb)
        elif cmd=='/help':_send(app,chat_id,help_page(),back_keyboard())
        elif cmd=='/status':_send(app,chat_id,status_page(app),back_keyboard())
        elif cmd=='/report':
            _require_master(app,chat_id);_send(app,chat_id,build_report_html(app),back_keyboard())
    except Exception as e:
        _send(app,chat_id,f"❌ {html.escape(str(e))}",control_keyboard(app) if cmd in {'/engine','/mode','/setmax','/setprofit','/setcopy','/setedge','/setage','/setcanary','/setscore'} else back_keyboard())
def menu_loop(app):
    if not app.telegram_bot_token:return
    try:
        info=get_webhook_info(app.telegram_bot_token)
        if info and info.get('url'):print('[telegram-menu] webhook configured; getUpdates menu not started');return
    except Exception as e:print('[telegram-menu] webhook check warning:',e)
    try:set_commands(app.telegram_bot_token)
    except Exception as e:print('[telegram-menu] command setup warning:',e)
    offset=None; print('[telegram-menu] interactive multi-chain menu started')
    while True:
        try:
            updates=get_updates(app.telegram_bot_token,limit=50,offset=offset,timeout=12)
            for u in updates:
                offset=int(u.get('update_id',0))+1
                try:handle_update(app,u)
                except Exception as e:print('[telegram-menu] update error:',type(e).__name__,e)
        except Exception as e:print('[telegram-menu] poll error:',type(e).__name__,e);time.sleep(3)
def start_menu_thread(app):
    if not app.telegram_bot_token:return None
    t=threading.Thread(target=menu_loop,args=(app,),daemon=True,name='telegram-menu');t.start();return t
