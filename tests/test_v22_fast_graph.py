from pathlib import Path
from types import SimpleNamespace

from learnerbot.market_scanner import _graph_triangles, _v2_venues
from learnerbot.config import AppSettings


def _addr(n): return "0x"+f"{n:040x}"


def test_graph_first_emits_only_real_triangles():
    w,a,b,c=_addr(1),_addr(2),_addr(3),_addr(4);factory=_addr(99)
    pools=[
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(101),'token0':w,'token1':a},
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(102),'token0':a,'token1':b},
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(103),'token0':b,'token1':w},
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(104),'token0':w,'token1':c},
    ]
    paths=_graph_triangles(pools,56,factory,w,[w,a,b,c],20)
    keys={tuple(x.lower() for x in p) for p in paths}
    assert tuple(x.lower() for x in [w,a,b,w]) in keys
    assert tuple(x.lower() for x in [w,b,a,w]) in keys
    assert all(c.lower() not in [x.lower() for x in p[1:-1]] for p in paths)


def test_graph_first_does_not_guess_missing_middle_pair():
    w,a,b=_addr(1),_addr(2),_addr(3);factory=_addr(99)
    pools=[
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(101),'token0':w,'token1':a},
        {'chain_id':'56','factory_address':factory,'pair_address':_addr(102),'token0':w,'token1':b},
    ]
    assert _graph_triangles(pools,56,factory,w,[w,a,b],20)==[]


def test_default_tokens_include_liquid_seeds_on_all_five_chains():
    root=Path(__file__).resolve().parents[1]
    import csv
    rows=list(csv.DictReader((root/'CSVbot'/'tokens.csv').open()))
    by={cid:[r for r in rows if r['chain_id']==cid and r.get('enabled','').lower()=='true'] for cid in {'1','56','137','8453','42161'}}
    assert all(any(r.get('role')=='wrapped_base' for r in rs) for rs in by.values())
    assert all(any(r.get('role')=='liquid_seed' for r in rs) for rs in by.values())
    assert all(len(rs)>=3 for rs in by.values())


def test_fast_market_defaults_enabled():
    root=Path(__file__).resolve().parents[1]
    import csv
    rows=list(csv.DictReader((root/'CSVbot'/'auto_trading_settings.csv').open()))
    vals={r['setting']:r['value'] for r in rows if r.get('chain_id')=='*'}
    assert vals['fast_market_enabled'].lower()=='true'
    assert int(vals['fast_market_interval_seconds'])==5
    assert int(vals['fast_market_pairs_per_dex_pass'])==0  # v2.3 moves discovery out of hot path


def test_all_default_v2_venues_are_registry_driven():
    root=Path(__file__).resolve().parents[1]
    app=AppSettings(root,root/'CSVbot',root/'data','',[],'')
    for cid in (1,56,137,8453,42161):
        venues=_v2_venues(app,cid)
        assert venues and all(v['router'].startswith('0x') and v['factory'].startswith('0x') for v in venues)


def test_fast_market_pass_merges_and_executes(monkeypatch,tmp_path):
    import learnerbot.fast_market as fm
    csv=tmp_path/'CSVbot';(csv/'auto').mkdir(parents=True)
    app=SimpleNamespace(csv_dir=csv,telegram_bot_token='',operator_settings=lambda:{'engine_enabled':'true'})
    live={'chain_id':'56','chain_slug':'bsc','router_address':_addr(9),'route_path':f'{_addr(1)}>{_addr(2)}>{_addr(3)}>{_addr(1)}','enabled':'true','expected_gross_profit_base':'0.001','slippage_reserve_base':'0'}
    monkeypatch.setattr(fm,'contexts',lambda *a,**k:[SimpleNamespace(conn=SimpleNamespace(close=lambda:None))])
    monkeypatch.setattr(fm,'scan_full_power_hot_routes',lambda app,ctxs:(csv/'auto'/'full_power_opportunities.csv',[live],[]))
    monkeypatch.setattr(fm,'merge_live_opportunities',lambda app,*groups:(csv/'live_opportunities.csv',[live]))
    monkeypatch.setattr(fm,'execute_best_live_opportunity',lambda app,rows:[])
    result=fm.run_fast_market_pass(app)
    assert result['status']=='OK' and result['routes']==1 and result['merged_routes']==1 and result['eligible']==1
    assert (csv/'auto'/'fast_market_status.csv').exists()
