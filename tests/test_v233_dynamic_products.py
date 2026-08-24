from __future__ import annotations

import csv
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from learnerbot.product_universe import refresh_product_universe, product_rows, allowed_product_addresses, route_product_policy

WBNB='0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c'
USDT='0x55d398326f99059fF775485246999027B3197955'
CAKE='0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82'
DYN='0x1111111111111111111111111111111111111111'
NEW='0x2222222222222222222222222222222222222222'
BAD='0x3333333333333333333333333333333333333333'
OTHER='0x4444444444444444444444444444444444444444'


def write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows(rows)


def fixture(tmp_path: Path, now: int):
    csvdir=tmp_path/'CSVbot';(csvdir/'auto').mkdir(parents=True)
    write_csv(csvdir/'auto_trading_settings.csv',['chain_id','setting','value','description'],[
        {'chain_id':'*','setting':'product_universe_enabled','value':'true','description':''},
        {'chain_id':'*','setting':'product_new_token_shadow_seconds','value':'900','description':''},
        {'chain_id':'*','setting':'product_established_age_seconds','value':'3600','description':''},
        {'chain_id':'*','setting':'product_established_min_pools','value':'3','description':''},
        {'chain_id':'*','setting':'product_strict_min_pools','value':'2','description':''},
    ])
    write_csv(csvdir/'tokens.csv',['chain_id','symbol','address','decimals','role','enabled'],[
        {'chain_id':'56','symbol':'WBNB','address':WBNB,'decimals':'18','role':'wrapped_base','enabled':'true'},
        {'chain_id':'56','symbol':'USDT','address':USDT,'decimals':'18','role':'liquid_seed','enabled':'true'},
        {'chain_id':'56','symbol':'CAKE','address':CAKE,'decimals':'18','role':'liquid_seed','enabled':'true'},
    ])
    v2=[]
    def add(pair,t0,t1,first,last):
        v2.append({'chain_id':'56','chain_slug':'bsc','dex_name':'PancakeSwap','router_address':'0x10ED43C718714eb63d5aA57B78B54704E256024E','factory_address':'0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73','pair_address':pair,'token0':t0,'token1':t1,'first_seen_epoch':first,'last_seen_epoch':last})
    old=now-7200;new=now-60
    add('0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',WBNB,DYN,old,now)
    add('0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab',DYN,USDT,old,now)
    add('0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaac',DYN,CAKE,old,now)
    add('0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',WBNB,NEW,new,now)
    add('0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbc',NEW,USDT,new,now)
    add('0xcccccccccccccccccccccccccccccccccccccccc',WBNB,BAD,old,now)
    add('0xcccccccccccccccccccccccccccccccccccccccd',BAD,USDT,old,now)
    add('0xccccccccccccccccccccccccccccccccccccccce',BAD,CAKE,old,now)
    write_csv(csvdir/'auto'/'pool_registry.csv',['chain_id','chain_slug','dex_name','router_address','factory_address','pair_address','token0','token1','first_seen_epoch','last_seen_epoch'],v2)
    write_csv(csvdir/'auto'/'v3_pool_registry.csv',['chain_id','chain_slug','dex_name','factory_address','router_address','quoter_address','fee','pool_address','token0','token1','last_seen_epoch'],[])
    write_csv(csvdir/'auto'/'execution_quarantine.csv',['observed_at_epoch','chain_id','route_id','token','kind','expires_at_epoch','reason'],[
        {'observed_at_epoch':now-10,'chain_id':'56','route_id':'','token':BAD,'kind':'TOKEN_BLOCK','expires_at_epoch':now+3600,'reason':'test'},
    ])
    write_csv(csvdir/'auto'/'auto_trade_simulations.csv',['timestamp_epoch','telegram_id','wallet_id','chain_id','chain_slug','route_id','route_path','input_base','min_net_profit_base','gross_profit_base','gas_cost_base','simulation_ok','reason'],[])
    app=SimpleNamespace(csv_dir=csvdir)
    ctx=SimpleNamespace(config=SimpleNamespace(chain_id=56,slug='bsc',wrapped_base_address=WBNB))
    return app,[ctx]


def by_addr(rows):
    return {r['address'].lower():r for r in rows}


def test_dynamic_product_levels_and_quarantine(tmp_path):
    now=int(time.time());app,ctxs=fixture(tmp_path,now)
    rows=refresh_product_universe(app,ctxs,now=now);m=by_addr(rows)
    assert m[WBNB.lower()]['risk_level']==1 and m[WBNB.lower()]['auto_trade']=='true'
    assert m[USDT.lower()]['risk_level']==1 and m[USDT.lower()]['category']=='CORE_STABLE'
    assert m[CAKE.lower()]['risk_level']==2 and m[CAKE.lower()]['auto_trade']=='true'
    assert m[DYN.lower()]['risk_level']==2 and m[DYN.lower()]['category']=='DISCOVERED_ESTABLISHED' and m[DYN.lower()]['auto_trade']=='true'
    assert m[NEW.lower()]['risk_level']==3 and m[NEW.lower()]['category']=='NEW_TOKEN_SHADOW' and m[NEW.lower()]['auto_trade']=='false'
    assert m[BAD.lower()]['risk_level']==4 and m[BAD.lower()]['auto_scan']=='false' and m[BAD.lower()]['auto_trade']=='false'


def test_allowed_products_and_fail_closed_route_policy(tmp_path):
    now=int(time.time());app,ctxs=fixture(tmp_path,now);refresh_product_universe(app,ctxs,now=now)
    auto=[x.lower() for x in allowed_product_addresses(app.csv_dir,56,include_shadow=False,max_tokens=50)]
    scan=[x.lower() for x in allowed_product_addresses(app.csv_dir,56,include_shadow=True,max_tokens=50)]
    assert DYN.lower() in auto and NEW.lower() not in auto and BAD.lower() not in auto
    assert NEW.lower() in scan and BAD.lower() not in scan
    assert route_product_policy(app.csv_dir,56,[WBNB,DYN,USDT,WBNB])['auto_trade'] is True
    assert route_product_policy(app.csv_dir,56,[WBNB,NEW,USDT,WBNB])['auto_trade'] is False
    assert route_product_policy(app.csv_dir,56,[WBNB,OTHER,USDT,WBNB])['auto_trade'] is False


def test_scanners_and_auto_executor_enforce_product_policy():
    root=Path(__file__).resolve().parents[1]
    scanner=(root/'learnerbot'/'full_power_scanner.py').read_text()
    auto=(root/'learnerbot'/'auto_trader.py').read_text()
    assert 'allowed_product_addresses' in scanner
    assert 'route_product_policy' in scanner
    assert "product_level3_max_price_impact_bps" in scanner
    assert 'route_product_policy' in auto and 'product_approved' in auto


def test_telegram_products_command_and_menu():
    root=Path(__file__).resolve().parents[1]
    ui=(root/'learnerbot'/'telegram_ui.py').read_text()
    tg=(root/'learnerbot'/'telegram.py').read_text()
    assert "def products_page" in ui
    assert "cmd=='/products'" in ui
    assert "menu:products" in ui
    assert '"command": "products"' in tg


def test_installer_does_not_copy_wallets_or_databases():
    root=Path(__file__).resolve().parents[1]
    path=root/'apply_v233_dynamic_products.sh'
    if not path.exists():
        pytest.skip('legacy one-shot installer is not part of the current repository checkout')
    s=path.read_text()
    forbidden=['cp -a data','cp -r data','*.sqlite3','user_wallets/','.live_wallet_store.key']
    assert all(x not in s for x in forbidden)
