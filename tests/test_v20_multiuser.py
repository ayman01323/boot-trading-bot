from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from learnerbot.user_registry import (
    join_user, get_user, update_user, set_user_setting, user_setting, user_bool,
    create_activation_code, redeem_activation_code, require_user,
)
from learnerbot.fee_engine import profit_share_amount
from learnerbot.auto_trader import _required_pre_fee_min, execute_best_live_opportunity
from learnerbot.live_executor import LiveTrader, LiveTradingError


def _base_csv(tmp_path: Path):
    csv=tmp_path/'CSVbot';csv.mkdir()
    (csv/'users.csv').write_text('telegram_id,role,status,fee_plan_id,label,allowed_chains,max_wallets,can_transfer,can_manual_trade,can_auto_trade,created_epoch,activated_epoch,notes\n')
    (csv/'activation_codes.csv').write_text('code_hash,plan_id,enabled,max_uses,uses,expires_epoch,notes\n')
    (csv/'fee_plans.csv').write_text('plan_id,enabled,activation_mode,activation_fee_native,activation_fee_bsc,activation_fee_base,profit_share_bps,fee_settlement_gas_reserve_base,notes\nSTANDARD,true,NONE,0,0,0,0,0.00005,x\nP10,true,CODE,0,0,0,1000,0.00005,x\n')
    (csv/'user_trading_settings.csv').write_text('telegram_id,chain_id,setting,value,description\n')
    (csv/'auto_trading_settings.csv').write_text('chain_id,setting,value,description\n*,auto_trading_enabled,false,safe\n')
    return csv


def test_join_update_and_chain_permission(tmp_path):
    csv=_base_csv(tmp_path)
    u=join_user(csv,123,'STANDARD')
    assert u['status']=='PENDING'
    update_user(csv,123,status='ACTIVE',allowed_chains='bsc,base')
    assert require_user(csv,123,active=True,chain_slug='bsc')['telegram_id']=='123'
    with pytest.raises(ValueError): require_user(csv,123,active=True,chain_slug='ethereum')


def test_user_setting_chain_override(tmp_path):
    csv=_base_csv(tmp_path);join_user(csv,123)
    set_user_setting(csv,123,'auto_input_base','0.001',chain_id='*')
    set_user_setting(csv,123,'auto_input_base','0.002',chain_id=56)
    set_user_setting(csv,123,'auto_trading_enabled','true',chain_id='*')
    assert user_setting(csv,123,56,'auto_input_base')=='0.002'
    assert user_setting(csv,123,8453,'auto_input_base')=='0.001'
    assert user_bool(csv,123,56,'auto_trading_enabled',False) is True


def test_canonical_global_scope_beats_stale_zero_alias(tmp_path):
    csv=_base_csv(tmp_path);join_user(csv,123)
    # Reproduce the production failure: a stale legacy chain_id=0 row remains
    # false while /autotrade updates the canonical '*' row to true.
    set_user_setting(csv,123,'auto_trading_enabled','true',chain_id='*')
    set_user_setting(csv,123,'auto_trading_enabled','false',chain_id='0')
    assert user_bool(csv,123,0,'auto_trading_enabled',False) is True
    assert user_bool(csv,123,8453,'auto_trading_enabled',False) is True

    # A real chain-specific setting must still override the global value.
    set_user_setting(csv,123,'auto_trading_enabled','false',chain_id=56)
    assert user_bool(csv,123,56,'auto_trading_enabled',False) is False
    assert user_bool(csv,123,8453,'auto_trading_enabled',False) is True


def test_activation_code_single_use(tmp_path):
    csv=_base_csv(tmp_path);join_user(csv,123,'STANDARD')
    code=create_activation_code(csv,'P10',max_uses=1)
    u=redeem_activation_code(csv,123,code)
    assert u['status']=='ACTIVE' and u['fee_plan_id']=='P10'
    with pytest.raises(ValueError): redeem_activation_code(csv,999,code)


def test_profit_share_only_positive_realised_net(tmp_path):
    csv=_base_csv(tmp_path);join_user(csv,123,'P10');update_user(csv,123,status='ACTIVE')
    assert profit_share_amount(csv,123,Decimal('1'))==Decimal('0.100000000000000000')
    assert profit_share_amount(csv,123,Decimal('-1'))==0


def test_pre_fee_min_reserves_profit_share_and_fee_gas():
    plan={'profit_share_bps':'1000','fee_settlement_gas_reserve_base':'0.00005'}
    required=_required_pre_fee_min(plan,Decimal('0.001'))
    assert required > Decimal('0.00105')


def test_auto_execution_master_gate_off_is_hard_stop(tmp_path):
    csv=_base_csv(tmp_path)
    app=SimpleNamespace(csv_dir=csv,data_dir=tmp_path/'data')
    # Even a nominally perfect opportunity cannot reach wallet/signing code while master AUTO is off.
    rows=[{'source_verified':'true','exact_quote_ok':'true','liquidity_ok':'true','whole_route_approved':'true'}]
    assert execute_best_live_opportunity(app,rows)==[]


def test_slippage_validation_without_rpc():
    t=LiveTrader.__new__(LiveTrader);t.settings={'slippage_bps':'100'}
    assert t._slippage_bps()==100
    t.settings={'slippage_bps':'6000'}
    with pytest.raises(LiveTradingError): t._slippage_bps()
