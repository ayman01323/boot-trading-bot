from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from learnerbot.capital_dashboard import _performance,_trading_state


class App:
    def __init__(self,root:Path):
        self.csv_dir=root
        self.data_dir=root/'data'
    def operator_settings(self):
        return {'engine_enabled':'true'}


def write(path:Path,text:str):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')


def test_realised_net_and_platform_fees(tmp_path):
    app=App(tmp_path)
    write(tmp_path/'chains.csv','chain_id,slug,name,type,enabled,explorer_url,native_symbol,wrapped_base_symbol,wrapped_base_address,finality_lag_blocks,scan_blocks_per_cycle\n56,bsc,BNB Smart Chain,EVM,true,,BNB,WBNB,0x0000000000000000000000000000000000000001,3,10\n')
    write(tmp_path/'auto'/'auto_trade_execution.csv','timestamp_epoch,telegram_id,wallet_id,chain_id,chain_slug,route_id,route_path,input_base,expected_gross_base,expected_gas_base,expected_net_base,realised_net_base,profit_fee_base,fee_tx_hash,tx_hash,status,note\n1,123,w1,56,bsc,r1,,0.01,,,,0.010,0.002,,,SUCCESS,\n2,123,w1,56,bsc,r2,,0.01,,,,0.500,0.100,,,FAILED,\n')
    write(tmp_path/'auto'/'fee_ledger.csv','timestamp_epoch,telegram_id,wallet_id,chain_id,fee_type,plan_id,gross_profit_base,gas_cost_base,net_profit_base,fee_amount_base,fee_asset,master_address,tx_hash,status,note\n1,123,,56,ACTIVATION,STANDARD,,,,0.001,NATIVE,,,CONFIRMED,\n')
    p=_performance(app,'123',{'w1'},{'bsc':Decimal('100')})
    assert p['trades']==1
    assert p['by_chain']['bsc']['net']==Decimal('0.008')
    assert p['net_usd']==Decimal('0.8')
    assert p['fees_usd']==Decimal('0.3')


def test_active_wallet_auto_state_requires_platform_gates(tmp_path):
    app=App(tmp_path);chain=SimpleNamespace(chain_id=56,slug='bsc')
    write(tmp_path/'user_trading_settings.csv','telegram_id,chain_id,setting,value,description\n123,*,live_trading_enabled,true,\n123,*,auto_trading_enabled,true,\n123,*,recommendation_mode,ARMED,\n')
    write(tmp_path/'live_trading_settings.csv','chain_id,setting,value,description\n*,trading_enabled,true,\n')
    write(tmp_path/'auto_trading_settings.csv','chain_id,setting,value,description\n*,auto_trading_enabled,true,\n')
    user={'telegram_id':'123','status':'ACTIVE','allowed_chains':'*','can_auto_trade':'true','can_manual_trade':'true'}
    wallet={'active':'true'}
    assert _trading_state(app,user,wallet,chain)=='AUTO'
