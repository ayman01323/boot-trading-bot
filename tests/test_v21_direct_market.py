from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from learnerbot.config import AppSettings, load_chains
from learnerbot.live_executor import V2_ROUTERS
from learnerbot.route_scanner import V2_FACTORY_FALLBACKS, _historical_cycle_variants, _scanner_input_base
from learnerbot.market_scanner import merge_live_opportunities, scan_direct_market_routes
from learnerbot.auto_trader import _eligible_scanner_candidate


def _addr(n):
    return "0x" + f"{n:040x}"


def test_all_five_evm_chains_enabled_and_rpc_configured():
    root=Path(__file__).resolve().parents[1]
    app=AppSettings(root,root/'CSVbot',root/'data','',[],'')
    chains=load_chains(app,enabled_only=True)
    assert {c.chain_id for c in chains} == {1,56,137,8453,42161}
    assert all(c.rpc_urls for c in chains)


def test_all_enabled_chains_have_v2_router_and_factory():
    assert set(V2_ROUTERS) >= {1,56,137,8453,42161}
    assert set(V2_FACTORY_FALLBACKS) >= {1,56,137,8453,42161}


def test_historical_cycle_variants_rotate_and_reverse():
    w,a,b=_addr(1),_addr(2),_addr(3)
    routes=_historical_cycle_variants([a,w,b],w,5)
    assert len(routes)==2
    assert all(r[0].lower()==w.lower() and r[-1].lower()==w.lower() for r in routes)


def test_scanner_input_uses_smaller_user_size(tmp_path):
    csv=tmp_path/'CSVbot';csv.mkdir()
    (csv/'users.csv').write_text('telegram_id,role,status,fee_plan_id,label,allowed_chains,max_wallets,can_transfer,can_manual_trade,can_auto_trade,created_epoch,activated_epoch,notes\n1,USER,ACTIVE,STANDARD,u,*,5,true,true,true,0,0,\n')
    (csv/'user_trading_settings.csv').write_text('telegram_id,chain_id,setting,value,description\n1,*,auto_input_base,0.00005,test\n')
    app=SimpleNamespace(csv_dir=csv)
    assert _scanner_input_base(app,56,{'auto_input_base':'0.005'}) == Decimal('0.00005')


def test_merge_prefers_stronger_duplicate(tmp_path):
    app=SimpleNamespace(csv_dir=tmp_path)
    base={'chain_id':'56','chain_slug':'bsc','router_address':_addr(9),'route_path':f'{_addr(1)}>{_addr(2)}>{_addr(3)}>{_addr(1)}','expected_gross_profit_base':'0.001','slippage_reserve_base':'0.0001','estimated_gas_base':'0','enabled':'true'}
    weak={**base,'wallet':'x'}
    strong={**base,'wallet':'DIRECT_MARKET','expected_gross_profit_base':'0.002'}
    path,rows=merge_live_opportunities(app,[weak],[strong])
    assert path.exists() and len(rows)==1 and rows[0]['wallet']=='DIRECT_MARKET'


def test_direct_market_scanner_returns_current_profitable_triangle(monkeypatch,tmp_path):
    import learnerbot.market_scanner as ms
    w,a,b=_addr(1),_addr(2),_addr(3)
    csv=tmp_path/'CSVbot';csv.mkdir();(csv/'auto').mkdir()
    (csv/'auto_trading_settings.csv').write_text('chain_id,setting,value,description\n*,direct_market_scanner_enabled,true,x\n*,direct_market_max_candidate_checks,20,x\n*,direct_market_max_routes_per_cycle,10,x\n*,max_price_impact_bps,200,x\n*,auto_input_base,0.001,x\n')
    (csv/'tokens.csv').write_text('chain_id,symbol,address,decimals,role,enabled\n56,W,W,18,x,true\n')
    (csv/'users.csv').write_text('telegram_id,role,status,fee_plan_id,label,allowed_chains,max_wallets,can_transfer,can_manual_trade,can_auto_trade,created_epoch,activated_epoch,notes\n')
    (csv/'user_trading_settings.csv').write_text('telegram_id,chain_id,setting,value,description\n')
    app=SimpleNamespace(csv_dir=csv)
    class Eth:
        def contract(self,*args,**kwargs): return object()
        def get_code(self,*args,**kwargs): return b'\x01'
    class W3: eth=Eth()
    class T:
        chain=SimpleNamespace(chain_id=56,slug='bsc',wrapped_base_address=w)
        router_address=_addr(9);wrapped=w;w3=W3()
        def _slippage_bps(self):return 10
        def cycle_quote(self,path,amount):
            # a->b route has a small positive edge; reverse is negative.
            positive=path[1].lower()==a.lower()
            out=Decimal(amount)*(Decimal('1.002') if positive else Decimal('0.998'))
            return {'gross_profit':out-Decimal(amount),'amount_out':out}
    monkeypatch.setattr(ms,'LiveTrader',lambda *a,**k:T())
    monkeypatch.setattr(ms,'_v2_venues',lambda *a,**k:[{'dex_name':'TestDEX','router':_addr(9),'factory':_addr(8)}])
    monkeypatch.setattr(ms,'_crawl_factory_pairs',lambda *a,**k:[])
    monkeypatch.setattr(ms,'_seed_factory_pairs',lambda trader,factory,dex,app,settings,pools,now:pools)
    monkeypatch.setattr(ms,'_token_universe',lambda *args,**kwargs:[w,a,b])
    monkeypatch.setattr(ms,'_graph_triangles',lambda *args,**kwargs:[[w,a,b,w],[w,b,a,w]])
    ctx=SimpleNamespace(config=SimpleNamespace(chain_id=56,slug='bsc',enabled=True))
    path,rows=scan_direct_market_routes(app,[ctx])
    assert path.exists();assert len(rows)==1
    assert rows[0]['wallet']=='DIRECT_MARKET'
    assert rows[0]['enabled']=='true' and rows[0]['exact_quote_ok']=='true'


def test_direct_market_scanner_rejects_negative_market(monkeypatch,tmp_path):
    import learnerbot.market_scanner as ms
    w,a,b=_addr(1),_addr(2),_addr(3)
    csv=tmp_path/'CSVbot';csv.mkdir();(csv/'auto').mkdir()
    (csv/'auto_trading_settings.csv').write_text('chain_id,setting,value,description\n*,direct_market_scanner_enabled,true,x\n*,auto_input_base,0.001,x\n')
    (csv/'tokens.csv').write_text('chain_id,symbol,address,decimals,role,enabled\n')
    (csv/'users.csv').write_text('telegram_id,role,status,fee_plan_id,label,allowed_chains,max_wallets,can_transfer,can_manual_trade,can_auto_trade,created_epoch,activated_epoch,notes\n')
    (csv/'user_trading_settings.csv').write_text('telegram_id,chain_id,setting,value,description\n')
    app=SimpleNamespace(csv_dir=csv)
    class Eth:
        def contract(self,*args,**kwargs): return object()
        def get_code(self,*args,**kwargs): return b'\x01'
    class W3: eth=Eth()
    class T:
        chain=SimpleNamespace(chain_id=56,slug='bsc',wrapped_base_address=w)
        router_address=_addr(9);wrapped=w;w3=W3()
        def _slippage_bps(self):return 10
        def cycle_quote(self,path,amount):
            out=Decimal(amount)*Decimal('0.999')
            return {'gross_profit':out-Decimal(amount),'amount_out':out}
    monkeypatch.setattr(ms,'LiveTrader',lambda *a,**k:T())
    monkeypatch.setattr(ms,'_v2_venues',lambda *a,**k:[{'dex_name':'TestDEX','router':_addr(9),'factory':_addr(8)}])
    monkeypatch.setattr(ms,'_crawl_factory_pairs',lambda *a,**k:[])
    monkeypatch.setattr(ms,'_seed_factory_pairs',lambda trader,factory,dex,app,settings,pools,now:pools)
    monkeypatch.setattr(ms,'_token_universe',lambda *args,**kwargs:[w,a,b])
    monkeypatch.setattr(ms,'_graph_triangles',lambda *args,**kwargs:[[w,a,b,w],[w,b,a,w]])
    ctx=SimpleNamespace(config=SimpleNamespace(chain_id=56,slug='bsc',enabled=True))
    _,rows=scan_direct_market_routes(app,[ctx])
    assert rows==[]
    assert (csv/'auto'/'direct_market_rejections.csv').exists()


def test_auto_candidate_filter_fails_closed():
    good={
        "enabled":"true",
        "scanner_exact":"true",
        "source_verified":"true",
        "exact_quote_ok":"true",
        "liquidity_ok":"true",
        "route_approved":"true",
        "whole_route_approved":"true",
    }
    assert _eligible_scanner_candidate(good)
    for key in tuple(good):
        missing=dict(good); missing.pop(key)
        assert not _eligible_scanner_candidate(missing), key
        false=dict(good); false[key]="false"
        assert not _eligible_scanner_candidate(false), key


def test_direct_market_budget_is_fair_across_enabled_chains(monkeypatch,tmp_path):
    import learnerbot.market_scanner as ms
    csv=tmp_path/'CSVbot';csv.mkdir();(csv/'auto').mkdir()
    (csv/'auto_trading_settings.csv').write_text(
        'chain_id,setting,value,description\n'
        '*,direct_market_scanner_enabled,true,x\n'
        '*,direct_market_max_candidate_checks,5,x\n'
        '*,direct_market_max_routes_per_cycle,5,x\n'
        '*,auto_input_base,0.001,x\n'
    )
    (csv/'tokens.csv').write_text('chain_id,symbol,address,decimals,role,enabled\n')
    (csv/'users.csv').write_text('telegram_id,role,status,fee_plan_id,label,allowed_chains,max_wallets,can_transfer,can_manual_trade,can_auto_trade,created_epoch,activated_epoch,notes\n')
    (csv/'user_trading_settings.csv').write_text('telegram_id,chain_id,setting,value,description\n')
    app=SimpleNamespace(csv_dir=csv)
    visited=[]
    class Eth:
        def contract(self,*args,**kwargs): return object()
        def get_code(self,*args,**kwargs): return b'\x01'
    class W3: eth=Eth()
    class T:
        def __init__(self,slug):
            cid={'bsc':56,'base':8453}[slug];visited.append(cid)
            self.chain=SimpleNamespace(chain_id=cid,slug=slug,wrapped_base_address=_addr(cid))
            self.router_address=_addr(cid+100);self.w3=W3()
        def _slippage_bps(self):return 10
        def cycle_quote(self,path,amount):
            out=Decimal(amount)*Decimal('0.999')
            return {'gross_profit':out-Decimal(amount),'amount_out':out}
    monkeypatch.setattr(ms,'LiveTrader',lambda app,slug,require_wallet=False,router_override=None:T(slug))
    monkeypatch.setattr(ms,'_v2_venues',lambda app,cid:[{'dex_name':'TestDEX','router':_addr(cid+100),'factory':_addr(cid+200)}])
    monkeypatch.setattr(ms,'_crawl_factory_pairs',lambda *a,**k:[])
    monkeypatch.setattr(ms,'_seed_factory_pairs',lambda trader,factory,dex,app,settings,pools,now:pools)
    monkeypatch.setattr(ms,'_token_universe',lambda ctx,app,trader,pools,settings,include_learned=True:[trader.chain.wrapped_base_address,_addr(9001),_addr(9002),_addr(9003)])
    monkeypatch.setattr(ms,'_graph_triangles',lambda pools,cid,factory,wrapped,universe,max_checks:[[wrapped,_addr(9001),_addr(9002),wrapped]])
    ctxs=[SimpleNamespace(config=SimpleNamespace(chain_id=56,slug='bsc',enabled=True)),SimpleNamespace(config=SimpleNamespace(chain_id=8453,slug='base',enabled=True))]
    ms.scan_direct_market_routes(app,ctxs)
    assert visited==[56,8453]
