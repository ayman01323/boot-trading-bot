from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


def write_cfg(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "auto_trading_settings.csv").write_text(
        "chain_id,setting,value,description\n"
        "*,execution_mismatch_route_quarantine_seconds,300,x\n"
        "*,execution_mismatch_token_strikes,3,x\n"
        "*,execution_mismatch_strike_window_seconds,1800,x\n"
        "*,execution_mismatch_token_quarantine_seconds,21600,x\n",
        encoding="utf-8",
    )


def test_specific_mismatch_detection(tmp_path, monkeypatch):
    from learnerbot.execution_quarantine import is_execution_mismatch
    assert is_execution_mismatch("PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT")
    assert not is_execution_mismatch("execution reverted: random transient problem")


def test_route_blocks_immediately_token_only_after_three_strikes(tmp_path):
    from learnerbot.execution_quarantine import record_execution_mismatch, quarantine_state, route_or_token_blocked
    csv_dir=tmp_path/'CSVbot';write_cfg(csv_dir)
    p=['0xwrap','0xbad','0xgood','0xwrap']
    reason='execution reverted: PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT'
    record_execution_mismatch(csv_dir,56,'r1',p,reason,now=1000)
    st=quarantine_state(csv_dir,56,now=1001)
    assert route_or_token_blocked(st,'r1',p)[0]
    assert '0xbad' not in st['tokens']
    record_execution_mismatch(csv_dir,56,'r2',p,reason,now=1010)
    record_execution_mismatch(csv_dir,56,'r3',p,reason,now=1020)
    st=quarantine_state(csv_dir,56,now=1021)
    assert '0xbad' in st['tokens'] and '0xgood' in st['tokens']


def test_expired_blocks_do_not_apply(tmp_path):
    from learnerbot.execution_quarantine import record_execution_mismatch, quarantine_state
    csv_dir=tmp_path/'CSVbot';write_cfg(csv_dir)
    record_execution_mismatch(csv_dir,56,'r1',['w','x','w'],'TRANSFER_FAILED',now=1000)
    st=quarantine_state(csv_dir,56,now=1400)
    assert 'r1' not in st['route_ids']


def test_zero_share_has_no_fee_settlement_reserve():
    from learnerbot.auto_trader import _required_pre_fee_min
    requested=Decimal('0.00000001')
    assert _required_pre_fee_min({'profit_share_bps':'0','fee_settlement_gas_reserve_base':'0.00005'},requested)==requested


def test_profit_share_still_reserves_fee_settlement():
    from learnerbot.auto_trader import _required_pre_fee_min
    got=_required_pre_fee_min({'profit_share_bps':'1000','fee_settlement_gas_reserve_base':'0.00005'},Decimal('0.000001'))
    assert got > Decimal('0.00005')


def test_scanner_filters_quarantine_before_quote():
    import learnerbot.full_power_scanner as m
    src=Path(m.__file__).read_text()
    q=src.index('blocked,why=route_or_token_blocked')
    quote=src.index('try:q=trader.cycle_quote', q)
    assert q < quote


def test_auto_trader_records_failed_v2_mismatch():
    import learnerbot.auto_trader as m
    src=Path(m.__file__).read_text()
    assert 'record_execution_mismatch(app.csv_dir' in src
    assert 'route_kind=="V2_CYCLE"' in src
