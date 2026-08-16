from __future__ import annotations
import argparse,shutil,sys,time
from pathlib import Path
from .config import AppSettings,ChainSettings,config_fingerprint,load_chains,load_kv_scoped
from .db import connect
from .rpc import RPCClient
from .scanner import scan
from .scoring import compute_scores
from .analyser import analyse_top_wallets
from .strategy import learn_patterns
from .behaviour import refresh_behaviour_research
from .copy_engine import refresh_copy_candidates,export_top20,generate_recommendations
from .exporter import export_chain,export_aggregate
from .etherscan import EtherscanV2,cache_wallet
from .intelligence import detect_and_send
from .multichain import contexts
from .report import build_report,send_report
from .telegram import get_me,recent_chats,send_to_chats,set_commands
from .telegram_ui import start_menu_thread,home_text,menu_keyboard
from .execution_queue import queue_armed_recommendations
from .route_scanner import scan_live_routes
from .market_scanner import _rows as market_file_rows, scan_direct_market_routes, merge_live_opportunities
from .auto_trader import execute_best_live_opportunity
from .fast_market import start_fast_market_thread
from .power_discovery import start_power_discovery_thread
from .user_registry import ensure_master_seed,all_users

def _acquire_run_lock(app):
    """Prevent two learnerbot run loops from writing the same chain DBs."""
    import fcntl
    app.data_dir.mkdir(parents=True, exist_ok=True)
    path = app.data_dir / ".learnerbot-run.lock"
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(
            "Another learnerbot run process is already active. "
            "Stop the existing systemd/manual process before starting another."
        )
    fh.seek(0)
    fh.truncate()
    fh.write(str(__import__("os").getpid()))
    fh.flush()
    return fh


def _app():return AppSettings.load()
def _enabled(app):return contexts(app,enabled_only=True,with_rpc=False)
def _find_chain(app,slug):
    for c in load_chains(app,enabled_only=False):
        if c.slug==slug:return c
    raise SystemExit(f'Unknown chain slug: {slug}')
def _runtime(app,slug,with_rpc=True):
    c=_find_chain(app,slug); s=ChainSettings.from_app_chain(app,c); conn=connect(s.db_path); rpc=RPCClient(c.rpc_urls,s.rpc_timeout_seconds,s.rpc_delay_ms) if with_rpc else None
    if with_rpc and not c.rpc_urls: raise SystemExit(f'{c.name}: no enabled RPC in CSVbot/rpc_endpoints.csv')
    return c,s,conn,rpc

def main(argv=None):
    parser=argparse.ArgumentParser(prog='learnerbot',description='Multi-user Telegram EVM learning/trading engine with v2.3.3 full-power V2/V3 + dynamic product universe, fast quotes, isolated wallets, fee plans and guarded execution')
    sub=parser.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init-db'); p=sub.add_parser('scan');p.add_argument('--chain');p.add_argument('--blocks',type=int,default=None)
    p=sub.add_parser('score');p.add_argument('--chain'); p=sub.add_parser('analyse');p.add_argument('--chain');p.add_argument('--top',type=int,default=20)
    p=sub.add_parser('learn');p.add_argument('--chain'); sub.add_parser('export'); p=sub.add_parser('top');p.add_argument('--chain');p.add_argument('--limit',type=int,default=25)
    p=sub.add_parser('etherscan-enrich');p.add_argument('--chain',required=True);p.add_argument('--wallet',required=True)
    sub.add_parser('telegram-chat-id');sub.add_parser('telegram-test');sub.add_parser('telegram-report');sub.add_parser('telegram-menu');sub.add_parser('report');sub.add_parser('chains');p=sub.add_parser('research');p.add_argument('--chain');p=sub.add_parser('rankings');p.add_argument('--chain');sub.add_parser('copy20');sub.add_parser('copy-signals')
    p=sub.add_parser('migrate-v13-bsc');p.add_argument('--source',required=True);p.add_argument('--force',action='store_true')
    p=sub.add_parser('run');p.add_argument('--sleep',type=int,default=None);p.add_argument('--top',type=int,default=None)
    args=parser.parse_args(argv);app=_app()
    if args.cmd=='chains':
        for c in load_chains(app,enabled_only=False):print(f"{c.slug}: enabled={c.enabled} chain_id={c.chain_id} rpc={len(c.rpc_urls)} name={c.name}")
        return 0
    if args.cmd=='migrate-v13-bsc':
        src=Path(args.source); target=app.data_dir/'bsc.sqlite3';app.data_dir.mkdir(parents=True,exist_ok=True)
        if not src.exists():print(f'Source not found: {src}',file=sys.stderr);return 2
        if target.exists() and not args.force:print(f'Target exists: {target}; add --force to replace',file=sys.stderr);return 2
        shutil.copy2(src,target);print(f'Migrated BSC database to {target}');return 0
    if args.cmd=='init-db':
        for c in load_chains(app,enabled_only=False):
            s=ChainSettings.from_app_chain(app,c);connect(s.db_path).close();print(f'{c.slug}: {s.db_path}')
        return 0
    if args.cmd in {'scan','score','analyse','learn','top'}:
        slugs=[args.chain] if getattr(args,'chain',None) else [c.slug for c in load_chains(app,enabled_only=True)]
        for slug in slugs:
            c,s,conn,rpc=_runtime(app,slug,with_rpc=args.cmd in {'scan','analyse'})
            if args.cmd=='scan':
                txs,start,target=scan(conn,rpc,args.blocks,c.finality_lag_blocks);print(f'[{slug}] scanned {start}..{target}; tx={txs}')
            elif args.cmd=='score':print(f'[{slug}] scored {compute_scores(conn,s.csv_dir,c.chain_id)} wallets')
            elif args.cmd=='analyse':print(f'[{slug}] {analyse_top_wallets(conn,rpc,s,args.top)}')
            elif args.cmd=='learn':print(f'[{slug}] learned/updated {learn_patterns(conn,s.learn_min_pattern_txs)} patterns')
            elif args.cmd=='top':
                for r in conn.execute('SELECT wallet,bot_score,tx_count,primary_executor FROM wallet_scores ORDER BY bot_score DESC LIMIT ?',(args.limit,)).fetchall():print(slug,r['wallet'],r['bot_score'],r['tx_count'],r['primary_executor'] or '')
        return 0
    if args.cmd in {'research','rankings'}:
        slugs=[args.chain] if getattr(args,'chain',None) else [c.slug for c in load_chains(app,enabled_only=True)]
        for slug in slugs:
            c,s,conn,_=_runtime(app,slug,False)
            if args.cmd=='research':
                print(f'[{slug}] {refresh_behaviour_research(conn,s)}')
            else:
                refresh_behaviour_research(conn,s)
                print(f'[{slug}] highest/fastest behaviour rankings')
                for r in conn.execute("""SELECT behaviour,total_net_base,profit_per_hour_base,positive_ratio,proven_count,overall_score,rank_profit,rank_speed,rank_overall FROM behaviour_rankings WHERE proven_count>0 ORDER BY rank_overall LIMIT 20""").fetchall():
                    print(r['rank_overall'],r['behaviour'],f"net={r['total_net_base']:.8f}",f"/h={r['profit_per_hour_base']:.8f}",f"positive={r['positive_ratio']*100:.1f}%",f"proof={r['proven_count']}",f"score={r['overall_score']:.1f}")
        return 0
    if args.cmd in {'copy20','copy-signals'}:
        ctxs=_enabled(app)
        for c in ctxs: refresh_copy_candidates(c.conn,c.settings)
        top_path,top_rows=export_top20(ctxs,app.csv_dir)
        if args.cmd=='copy20':
            print(top_path)
            for r in top_rows:
                print(r['global_rank'],r['chain_slug'],r['wallet'],r['behaviour'],f"score={float(r['copy_score']):.1f}",f"positive={float(r['positive_ratio'])*100:.1f}%",f"proof={int(r['proven_count'])}",f"net={float(r['total_net_base']):.8f}")
        else:
            path,recs=generate_recommendations(ctxs,app.csv_dir);print(path)
            for r in recs: print(r['action'],r['chain_slug'],r['wallet'],r['behaviour'],f"net={float(r['conservative_net_profit_base']):.8f}",r['reason'])
        return 0
    if args.cmd=='export':
        ctxs=_enabled(app); paths=[]
        for c in ctxs:paths+=export_chain(c.conn,c.config,app.csv_dir)
        paths+=export_aggregate(ctxs,app.csv_dir)
        for p in paths:print(p)
        return 0
    if args.cmd=='etherscan-enrich':
        c,s,conn,_=_runtime(app,args.chain,False);api=EtherscanV2(app.etherscan_api_key,c.chain_id);data=cache_wallet(conn,api,args.wallet);print({k:len(v.get('result',[])) if isinstance(v.get('result'),list) else v.get('message') for k,v in data.items()});return 0
    if args.cmd=='telegram-chat-id':
        me=get_me(app.telegram_bot_token);print(f"Bot: @{me.get('username','')}"); chats=recent_chats(app.telegram_bot_token,50)
        for c in chats:print(f"chat_id={c['id']} type={c['type']} title={c['title']}")
        return 0 if chats else 1
    if args.cmd=='telegram-test':
        me=get_me(app.telegram_bot_token);set_commands(app.telegram_bot_token);result=send_to_chats(app.telegram_bot_token,app.telegram_chat_ids,f"✅ Multi-Chain Learning Bot connection works.\nBot: @{me.get('username','')}\nMode: v2.3.3 FULL-POWER + DYNAMIC PRODUCTS + GUARDED AUTO TRADING")
        print(f"Telegram test: {result['sent_chats']} succeeded, {result['failed_chats']} failed");return 0 if result['sent_chats'] else 1
    if args.cmd=='telegram-menu':
        result=send_to_chats(app.telegram_bot_token,app.telegram_chat_ids,home_text(),parse_mode='HTML',reply_markup=menu_keyboard());print(f"Telegram menu: {result['sent_chats']} succeeded, {result['failed_chats']} failed");return 0 if result['sent_chats'] else 1
    if args.cmd=='report':print(build_report(app));return 0
    if args.cmd=='telegram-report':ok,msg=send_report(app,True);print(msg);return 0 if ok else 1
    if args.cmd=='run':
        run_lock = _acquire_run_lock(app)
        ensure_master_seed(app)
        print('Starting multi-chain learning loop v2.3.3. Dynamic risk-tiered products + parallel hot V2/V3 market discovery + cross-DEX shadow detection + configurable gas bidding are ON; signing remains gate-controlled.')
        start_menu_thread(app);start_power_discovery_thread(app);start_fast_market_thread(app);cycle=0;last_fp=None
        while True:
            try:
                # Hot reload all operational CSV configuration every cycle.
                app=AppSettings.load(); fp=config_fingerprint(app)
                if fp!=last_fp: print('[config] CSVbot configuration loaded/reloaded');last_fp=fp
                op=app.operator_settings(); engine_on=str(op.get('engine_enabled','true')).lower() in {'1','true','yes','on'}
                if not engine_on:
                    print('[control] learning engine PAUSED by operator; Telegram control remains available')
                    sleep=args.sleep if args.sleep is not None else int(app.get('run_sleep_seconds','15',int));time.sleep(max(5,sleep));continue
                ctxs=contexts(app,enabled_only=True,with_rpc=True)
                healthy=[]
                for c in ctxs:
                    try:
                        if c.rpc is None:
                            raise RuntimeError("no enabled EVM RPC endpoint in CSVbot/rpc_endpoints.csv")
                        txs,start,target=scan(c.conn,c.rpc,None,c.config.finality_lag_blocks,max_blocks=c.config.scan_blocks_per_cycle);print(f"[scan:{c.config.slug}] {start}..{target} tx={txs}")
                        n=compute_scores(c.conn,c.settings.csv_dir,c.config.chain_id);print(f"[score:{c.config.slug}] wallets={n}")
                        risk=load_kv_scoped(c.settings.app.csv_dir/'risk_settings.csv',c.config.chain_id); top=args.top or int(risk.get('max_wallets_per_chain_cycle','20')); result=analyse_top_wallets(c.conn,c.rpc,c.settings,top);print(f"[analyse:{c.config.slug}] {result}")
                        learned=learn_patterns(c.conn,c.settings.learn_min_pattern_txs);print(f"[learn:{c.config.slug}] patterns={learned}")
                        research=refresh_behaviour_research(c.conn,c.settings);print(f"[research:{c.config.slug}] {research}")
                        copy_result=refresh_copy_candidates(c.conn,c.settings);print(f"[copy-check:{c.config.slug}] {copy_result}")
                        export_chain(c.conn,c.config,app.csv_dir);healthy.append(c)
                    except Exception as chain_exc:
                        print(f"[chain-error:{c.config.slug}] {type(chain_exc).__name__}: {chain_exc}",file=sys.stderr)
                export_aggregate(ctxs,app.csv_dir)
                top_path,top_rows=export_top20(healthy or ctxs,app.csv_dir)
                try:
                    learned_path,learned_rows=scan_live_routes(app,healthy or ctxs)
                except Exception as learned_exc:
                    learned_rows=[]
                    print(f'[learned-route-error] {type(learned_exc).__name__}: {learned_exc}',file=sys.stderr)
                auto_cfg=load_kv_scoped(Path(app.csv_dir)/'auto_trading_settings.csv',0)
                fast_on=str(auto_cfg.get('fast_market_enabled','true')).lower() in {'1','true','yes','on'}
                if fast_on:
                    market_rows=market_file_rows(Path(app.csv_dir)/'auto'/'full_power_opportunities.csv')
                else:
                    try:
                        market_path,market_rows=scan_direct_market_routes(app,healthy or ctxs,fast=False)
                        print(f'[direct-market-scan] routes={len(market_rows)} file={market_path}')
                    except Exception as market_exc:
                        market_rows=[]
                        print(f'[direct-market-error] {type(market_exc).__name__}: {market_exc}',file=sys.stderr)
                opp_path,live_rows=merge_live_opportunities(app,learned_rows,market_rows)
                live_good=sum(1 for r in live_rows if str(r.get('enabled','')).lower()=='true')
                print(f'[live-route-scan] learned={len(learned_rows)} fast-direct={len(market_rows)} routes={len(live_rows)} eligible-for-wallet-sim={live_good} file={opp_path}')
                rec_path,recs=generate_recommendations(healthy or ctxs,app.csv_dir)
                queue_result=queue_armed_recommendations(app,recs)
                auto_events=[] if fast_on else execute_best_live_opportunity(app,live_rows)
                print(f'[copy-top20] approved={len(top_rows)} file={top_path}')
                if recs: print(f'[copy-signals] recommendations={len(recs)} file={rec_path}')
                if queue_result['added']: print(f"[execution-queue] added={queue_result['added']} pending-file={queue_result['path']}")
                for ev in auto_events:
                    print(f"[auto-trade] {ev.get('status')} {ev.get('chain_slug')} route={ev.get('route_id')} tx={ev.get('tx_hash') or '-'} note={ev.get('note')}")
                    if ev.get('status') in {'BROADCAST','SUCCESS','SUCCESS_FEE_PENDING'} and app.telegram_bot_token and ev.get('telegram_id'):
                        send_to_chats(app.telegram_bot_token,[str(ev.get('telegram_id'))],
                            f"⚡ AUTO ROUTE {ev.get('status')}\nChain: {ev.get('chain_slug','').upper()}\nInput: {ev.get('input_base')}\nRealised net: {ev.get('realised_net_base') or ev.get('expected_net_base')}\nProfit fee: {ev.get('profit_fee_base') or 0}\nTX: {ev.get('tx_hash')}")
                cycle+=1
                g=app.general(); enabled=str(g.get('telegram_report_enabled','true')).lower() in {'1','true','yes','on'}; every=max(1,int(g.get('telegram_report_every_cycles','1')));mode=g.get('telegram_report_mode','meaningful').strip().lower()
                if cycle%every==0:
                    if mode=='all':
                        if enabled:
                            ok,msg=send_report(app,True);print('[telegram]',msg)
                        else:
                            print('[telegram] automatic full reports=OFF')
                    else:
                        # detect_and_send snapshots every cadence even while master output is OFF,
                        # preventing old changes from flooding Telegram when it is re-enabled.
                        alerts=detect_and_send(app,ctxs,initial_silent=True);print(f'[telegram] meaningful alerts={len(alerts)} master={"ON" if enabled else "OFF"}')
                for _ctx in ctxs:
                    try:
                        _ctx.conn.close()
                    except Exception:
                        pass
            except KeyboardInterrupt:print('Stopped.');return 0
            except Exception as e:print(f'[error] {type(e).__name__}: {e}',file=sys.stderr)
            sleep=args.sleep if args.sleep is not None else int(app.get('run_sleep_seconds','15',int));time.sleep(max(5,sleep))
    return 0
