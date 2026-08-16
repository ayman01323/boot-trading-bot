#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CSV=ROOT/'CSVbot'

CHAINS=[
 {'chain_id':'56','slug':'bsc','name':'BNB Smart Chain','type':'EVM','enabled':'true','explorer_url':'https://bscscan.com','native_symbol':'BNB','wrapped_base_symbol':'WBNB','wrapped_base_address':'0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c','finality_lag_blocks':'3','scan_blocks_per_cycle':'10','notes':'Enabled v2.2'},
 {'chain_id':'8453','slug':'base','name':'Base','type':'EVM','enabled':'true','explorer_url':'https://base.blockscout.com','native_symbol':'ETH','wrapped_base_symbol':'WETH','wrapped_base_address':'0x4200000000000000000000000000000000000006','finality_lag_blocks':'3','scan_blocks_per_cycle':'10','notes':'Enabled v2.2'},
 {'chain_id':'1','slug':'ethereum','name':'Ethereum','type':'EVM','enabled':'true','explorer_url':'https://etherscan.io','native_symbol':'ETH','wrapped_base_symbol':'WETH','wrapped_base_address':'0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2','finality_lag_blocks':'3','scan_blocks_per_cycle':'5','notes':'Enabled v2.2'},
 {'chain_id':'42161','slug':'arbitrum','name':'Arbitrum One','type':'EVM','enabled':'true','explorer_url':'https://arbiscan.io','native_symbol':'ETH','wrapped_base_symbol':'WETH','wrapped_base_address':'0x82aF49447D8a07e3bd95BD0d56f35241523fBab1','finality_lag_blocks':'3','scan_blocks_per_cycle':'10','notes':'Enabled v2.2'},
 {'chain_id':'137','slug':'polygon','name':'Polygon PoS','type':'EVM','enabled':'true','explorer_url':'https://polygonscan.com','native_symbol':'POL','wrapped_base_symbol':'WPOL','wrapped_base_address':'0x0d500B1d8E8eD2A1E4bD6A0B1B0D6c2f74a27d3F','finality_lag_blocks':'64','scan_blocks_per_cycle':'10','notes':'Enabled v2.2'},
]
DEX=[
 ('56','PancakeSwap','V2','0x10ED43C718714eb63d5aA57B78B54704E256024E','0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73'),
 ('8453','PancakeSwap','V2','0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb','0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E'),
 ('1','PancakeSwap','V2','0xEfF92A263d31888d860bD50809A8D171709b7b1c','0x1097053Fd2ea711dad45caCcc45EfF7548fCB362'),
 ('42161','PancakeSwap','V2','0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb','0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E'),
 ('137','QuickSwap','V2','0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff','0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32'),
]
ROUTERS={x[0]:x[3] for x in DEX}
RESERVES={'56':'0.0007','8453':'0.00012','1':'0.003','42161':'0.0002','137':'0.1'}
AUTO={
 'direct_market_scanner_enabled':'true','direct_market_max_tokens_per_chain':'32',
 'direct_market_pairs_per_dex_cycle':'24','direct_market_bootstrap_tail_pairs':'120',
 'direct_market_recent_pairs_watch':'10',
 'direct_market_max_candidate_checks':'300','direct_market_max_routes_per_cycle':'30',
 'direct_market_min_edge_base':'0','scanner_input_base':'0','max_variants_per_fingerprint':'48',
 'max_candidate_checks_per_cycle':'800',
 'fast_market_enabled':'true','fast_market_interval_seconds':'15',
 'fast_market_max_candidate_checks':'120','fast_market_max_routes_per_pass':'20',
 'fast_market_pairs_per_dex_pass':'6','direct_market_max_seed_tokens_per_chain':'8',
 'direct_market_seed_pair_checks_per_venue':'28',
}

def read(path):
 if not path.exists(): return [],[]
 with path.open('r',encoding='utf-8-sig',newline='') as f:
  r=csv.DictReader(f);return list(r),list(r.fieldnames or [])

def write(path,rows,fields):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp')
 with tmp.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])
 tmp.replace(path)

def merge_scoped(path, values, descriptions=None, force=None):
 rows,fields=read(path)
 if not fields: fields=['chain_id','setting','value','description']
 descriptions=descriptions or {};force=set(force or [])
 by={(str(r.get('chain_id') or '*'),str(r.get('setting') or '')):r for r in rows}
 for (scope,key),val in values.items():
  r=by.get((scope,key))
  if r is None:
   r={'chain_id':scope,'setting':key,'value':val,'description':descriptions.get(key,'v2.2 setting')};rows.append(r);by[(scope,key)]=r
  elif key in force: r['value']=val
 write(path,rows,fields)

# Chains: v2.2 explicitly turns all five supported EVM chains on.
write(CSV/'chains.csv',CHAINS,list(CHAINS[0]))

# DEX registry: replace/add the primary executable V2 venue for each supported chain,
# while leaving unrelated future DEX rows intact.
rows,fields=read(CSV/'dex_registry.csv')
if not fields: fields=['chain_id','dex_name','version','router','factory','enabled','notes']
keys={(r.get('chain_id'),(r.get('dex_name') or '').lower(),(r.get('version') or '').lower()):r for r in rows}
for cid,name,ver,router,factory in DEX:
 key=(cid,name.lower(),ver.lower());r=keys.get(key)
 if r is None:
  r={'chain_id':cid,'dex_name':name,'version':ver};rows.append(r);keys[key]=r
 r.update({'router':router,'factory':factory,'enabled':'true','notes':'v2.2 primary direct-market/execution venue'})
write(CSV/'dex_registry.csv',rows,fields)

# Wrapped base + liquid seed tokens. Existing user token rows are preserved.
SEEDS=[
 ('56','WBNB','0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c','18','wrapped_base'),
 ('56','CAKE','0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82','18','liquid_seed'),
 ('56','USDT','0x55d398326f99059fF775485246999027B3197955','18','liquid_seed'),
 ('8453','WETH','0x4200000000000000000000000000000000000006','18','wrapped_base'),
 ('8453','USDC','0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913','6','liquid_seed'),
 ('8453','CAKE','0x3055913c90Fcc1A6CE9a358911721eEb942013A1','18','liquid_seed'),
 ('1','WETH','0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2','18','wrapped_base'),
 ('1','USDC','0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48','6','liquid_seed'),
 ('1','CAKE','0x152649eA73beAb28c5b49B26eb48f7EAD6d4c898','18','liquid_seed'),
 ('42161','WETH','0x82aF49447D8a07e3bd95BD0d56f35241523fBab1','18','wrapped_base'),
 ('42161','USDC','0xaf88d065e77c8cC2239327C5EDb3A432268e5831','6','liquid_seed'),
 ('42161','CAKE','0x1b896893dfc86bb67Cf57767298b9073D2c1bA2c','18','liquid_seed'),
 ('137','WPOL','0x0d500B1d8E8eD2A1E4bD6A0B1B0D6c2f74a27d3F','18','wrapped_base'),
 ('137','USDC','0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359','6','liquid_seed'),
 ('137','WETH','0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619','18','liquid_seed'),
]
rows,fields=read(CSV/'tokens.csv')
if not fields: fields=['chain_id','symbol','address','decimals','role','enabled']
by={(str(r.get('chain_id') or ''),(r.get('address') or '').lower()):r for r in rows}
for cid,symbol,address,decimals,role in SEEDS:
 key=(cid,address.lower());r=by.get(key)
 if r is None:
  r={'chain_id':cid,'address':address};rows.append(r);by[key]=r
 r.update({'symbol':symbol,'decimals':decimals,'role':role,'enabled':'true'})
write(CSV/'tokens.csv',rows,fields)

# Live settings: add routers/reserves for all five, but always reset the MASTER live gate OFF on upgrade.
vals={("*",'trading_enabled'):'false'}
for cid,router in ROUTERS.items(): vals[(cid,'router_address')]=router
for cid,reserve in RESERVES.items(): vals[(cid,'min_native_gas_reserve')]=reserve
merge_scoped(CSV/'live_trading_settings.csv',vals,force={'trading_enabled','router_address'})

# Automatic settings: add direct-market controls and always reset the MASTER auto gate OFF.
vals={("*",'auto_trading_enabled'):'false'}
vals.update({('*',k):v for k,v in AUTO.items()})
merge_scoped(CSV/'auto_trading_settings.csv',vals,force={'auto_trading_enabled'})

# Master wallet rows exist for all chains, but addresses remain operator-controlled and disabled unless already configured.
rows,fields=read(CSV/'master_wallets.csv')
if not fields: fields=['chain_id','chain_slug','address','enabled','description']
by={str(r.get('chain_id') or ''):r for r in rows}
for c in CHAINS:
 r=by.get(c['chain_id'])
 if r is None:
  r={'chain_id':c['chain_id'],'chain_slug':c['slug'],'address':'','enabled':'false','description':f"Master fee wallet for {c['name']}"};rows.append(r);by[c['chain_id']]=r
write(CSV/'master_wallets.csv',rows,fields)

# Ensure v2 registries exist without creating any user or private key.
if not (CSV/'users.csv').exists():
 write(CSV/'users.csv',[],['telegram_id','role','status','fee_plan_id','label','allowed_chains','max_wallets','can_transfer','can_manual_trade','can_auto_trade','created_epoch','activated_epoch','notes'])
if not (CSV/'user_trading_settings.csv').exists():
 write(CSV/'user_trading_settings.csv',[],['telegram_id','chain_id','setting','value','description'])
print('v2.2 CSV configuration merged; all five EVM chains enabled; fast graph scanner ON; MASTER LIVE/AUTO reset OFF')
