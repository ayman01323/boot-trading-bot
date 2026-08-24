from pathlib import Path
import csv
import pytest

ROOT=Path(__file__).resolve().parents[1]

def _kv(path):
    if not path.exists():
        pytest.skip(f'server-local runtime configuration not present in hosted CI: {path.name}')
    out={}
    with path.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r.get('chain_id')=='*': out[r.get('setting')]=r.get('value')
    return out

def test_speed_defaults():
    cfg=_kv(ROOT/'CSVbot'/'auto_trading_settings.csv')
    assert cfg['fast_market_interval_seconds']=='5'
    assert int(cfg['fast_market_max_candidate_checks']) <= 100
    assert cfg['fast_market_pairs_per_dex_pass']=='0'
    assert cfg['direct_market_seed_pair_checks_per_venue']=='0'

def test_gas_bid_default():
    cfg=_kv(ROOT/'CSVbot'/'live_trading_settings.csv')
    assert cfg['gas_bid_multiplier']=='1.25'

def test_gas_bid_code_and_preflight_order():
    s=(ROOT/'learnerbot'/'live_executor.py').read_text()
    assert 'def _gas_bid_multiplier' in s
    assert 'suggested_priority * bid_mult' in s
    assert 'self.w3.eth.gas_price) * bid_mult' in s
    assert 'AUTO_PREFLIGHT' in s
    assert 'self.w3.eth.call(call_tx)' in s
    call_i=s.index('self.w3.eth.call(call_tx)')
    sign_i=s.index('txh = self._sign_send(built)', call_i)
    assert call_i < sign_i

def test_telegram_gas_bid_command():
    s=(ROOT/'learnerbot'/'telegram_ui.py').read_text()
    assert "cmd=='/setgasbid'" in s
    assert 'Gas bid:' in s
