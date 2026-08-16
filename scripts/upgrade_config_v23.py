from __future__ import annotations
import csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CSV=ROOT/'CSVbot'

DEX_ROWS=[
 {'chain_id':'56','dex_name':'PancakeSwap','version':'V2','router':'0x10ED43C718714eb63d5aA57B78B54704E256024E','factory':'0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73','quoter':'','enabled':'true','auto_execute':'true','notes':'v2.3 live V2 single-router cycles'},
 {'chain_id':'56','dex_name':'PancakeSwap','version':'V3','router':'0x1b81D678ffb9C0263b24A97847620C99d213eB14','factory':'0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865','quoter':'0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997','enabled':'true','auto_execute':'true','notes':'v2.3 live V3 single-router cycles with final eth_call'},
 {'chain_id':'8453','dex_name':'PancakeSwap','version':'V2','router':'0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb','factory':'0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E','quoter':'','enabled':'true','auto_execute':'true','notes':'v2.3 live V2 single-router cycles'},
 {'chain_id':'8453','dex_name':'PancakeSwap','version':'V3','router':'0x1b81D678ffb9C0263b24A97847620C99d213eB14','factory':'0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865','quoter':'0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997','enabled':'true','auto_execute':'true','notes':'v2.3 live V3 single-router cycles with final eth_call'},
 {'chain_id':'8453','dex_name':'QuickSwap','version':'V2','router':'0x4a012af2b05616Fb390ED32452641C3F04633bb5','factory':'0xEC6540261aaaE13F236A032d454dc9287E52e56A','quoter':'','enabled':'true','auto_execute':'false','notes':'cross-DEX discovery only until atomic executor deployed'},
 {'chain_id':'1','dex_name':'PancakeSwap','version':'V2','router':'0xEfF92A263d31888d860bD50809A8D171709b7b1c','factory':'0x1097053Fd2ea711dad45caCcc45EfF7548fCB362','quoter':'','enabled':'true','auto_execute':'true','notes':'v2.3 live V2 single-router cycles'},
 {'chain_id':'1','dex_name':'PancakeSwap','version':'V3','router':'0x1b81D678ffb9C0263b24A97847620C99d213eB14','factory':'0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865','quoter':'0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997','enabled':'true','auto_execute':'true','notes':'v2.3 live V3 single-router cycles with final eth_call'},
 {'chain_id':'1','dex_name':'Uniswap','version':'V2','router':'0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D','factory':'0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f','quoter':'','enabled':'true','auto_execute':'false','notes':'cross-DEX discovery only until atomic executor deployed'},
 {'chain_id':'42161','dex_name':'PancakeSwap','version':'V2','router':'0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb','factory':'0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E','quoter':'','enabled':'true','auto_execute':'true','notes':'v2.3 live V2 single-router cycles'},
 {'chain_id':'42161','dex_name':'PancakeSwap','version':'V3','router':'0x1b81D678ffb9C0263b24A97847620C99d213eB14','factory':'0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865','quoter':'0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997','enabled':'true','auto_execute':'true','notes':'v2.3 live V3 single-router cycles with final eth_call'},
 {'chain_id':'137','dex_name':'QuickSwap','version':'V2','router':'0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff','factory':'0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32','quoter':'','enabled':'true','auto_execute':'true','notes':'v2.3 live V2 single-router cycles'},
 {'chain_id':'137','dex_name':'QuickSwap','version':'V3_ALGEBRA','router':'0xf5b509bB0909a69B1c207E495f687a596C168E12','factory':'0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28','quoter':'0xa15F0D7377B2A0C0c10db057f641beD21028FC89','enabled':'true','auto_execute':'false','notes':'configured scan-only; Algebra is not treated as Pancake/Uniswap V3'},
]

AUTO={
 'fast_market_enabled':('true','Independent parallel hot V2/V3 quote loop'),
 'fast_market_interval_seconds':('5','Target seconds between hot quote passes; no factory crawling occurs in hot path'),
 'fast_market_max_candidate_checks':('80','Total hot V2/V3/cross-DEX quote budget across enabled chains'),
 'fast_market_max_routes_per_pass':('30','Maximum profitable direct rows retained per hot pass'),
 'fast_market_pairs_per_dex_pass':('0','Factory crawling removed from hot path; background discovery owns pool updates'),
 'direct_market_seed_pair_checks_per_venue':('0','No repeated V2 seed getPair calls in hot path'),
 'full_power_enabled':('true','Enable parallel V2/V3 hot search plus cross-DEX shadow detection'),
 'v3_scanner_enabled':('true','Discover and quote PancakeSwap V3 on BSC Ethereum Arbitrum Base'),
 'v3_fee_tiers':('100,500,2500,10000','Pancake/Uniswap-style V3 fee tiers tested after factory pool validation'),
 'full_power_max_seed_tokens':('6','Maximum operator-approved liquid seeds used for V3 pool discovery'),
 'full_power_parallel_chains':('5','Maximum chain hot-quote workers'),
 'full_power_discovery_interval_seconds':('120','Background V2/V3 pool discovery interval'),
 'full_power_v2_discovery_pairs_per_venue':('8','Background V2 factory crawl per venue/discovery pass'),
 'full_power_v2_seed_pair_checks':('18','Background canonical V2 seed-pair checks per venue/discovery pass'),
 'cross_dex_scanner_enabled':('true','Detect multi-router V2 opportunities; shadow-only until atomic executor deployment'),
}

def read(path):
    if not path.exists():return [],[]
    with path.open(encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);return list(rd),list(rd.fieldnames or [])

def write(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])

def migrate_dex():
    p=CSV/'dex_registry.csv';rows,fields=read(p)
    required=['chain_id','dex_name','version','router','factory','quoter','enabled','auto_execute','notes']
    fields=list(dict.fromkeys(fields+required))
    # replace exact chain+dex+version rows for official v2.3 venues; preserve unrelated operator venues.
    keys={(r['chain_id'],r['dex_name'].lower(),r['version'].upper()) for r in DEX_ROWS}
    rows=[r for r in rows if (str(r.get('chain_id')),str(r.get('dex_name','')).lower(),str(r.get('version','')).upper()) not in keys]
    rows.extend(DEX_ROWS);write(p,rows,fields)

def set_kv(path,vals):
    rows,fields=read(path);fields=fields or ['chain_id','setting','value','description'];by={(r.get('chain_id'),r.get('setting')):r for r in rows}
    for k,(v,d) in vals.items():
        row=by.get(('*',k))
        if row is None:row={'chain_id':'*','setting':k};rows.append(row);by[('*',k)]=row
        row['value']=v;row['description']=d
    write(path,rows,fields)

def main():
    migrate_dex();set_kv(CSV/'auto_trading_settings.csv',AUTO);set_kv(CSV/'live_trading_settings.csv',{'gas_bid_multiplier':('1.25','Fee-price bid multiplier; included in simulation')})
    # Fail-safe after upgrade. User/master must re-enable only after /autoprep re-approves the V3 router.
    set_kv(CSV/'auto_trading_settings.csv',{'auto_trading_enabled':('false','MASTER auto gate reset OFF by v2.3 upgrade')})
    set_kv(CSV/'live_trading_settings.csv',{'trading_enabled':('false','MASTER live gate reset OFF by v2.3 upgrade')})
    print('v2.3 config migration complete: V2/V3 full-power enabled; MASTER LIVE/AUTO reset OFF.')
if __name__=='__main__':main()
