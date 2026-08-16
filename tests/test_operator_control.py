from pathlib import Path
from types import SimpleNamespace

from learnerbot.operator_control import set_kv, set_scoped_default, set_chain_enabled
from learnerbot.execution_queue import queue_armed_recommendations, queue_summary


def test_scoped_and_chain_updates_are_atomic(tmp_path: Path):
    copy = tmp_path / 'copy_settings.csv'
    copy.write_text('chain_id,setting,value,description\n*,recommendation_mode,SHADOW,mode\n', encoding='utf-8')
    set_scoped_default(copy, 'recommendation_mode', 'ARMED')
    text = copy.read_text(encoding='utf-8')
    assert '*,recommendation_mode,ARMED,mode' in text

    chains = tmp_path / 'chains.csv'
    chains.write_text('chain_id,slug,enabled\n56,bsc,true\n8453,base,false\n', encoding='utf-8')
    set_chain_enabled(chains, 8453, True)
    assert '8453,base,true' in chains.read_text(encoding='utf-8')


def test_execution_queue_only_queues_armed_in_once(tmp_path: Path):
    csv_dir = tmp_path / 'CSVbot'
    csv_dir.mkdir()
    (csv_dir / 'operator_settings.csv').write_text(
        'setting,value,description\nexecution_queue_enabled,true,x\n', encoding='utf-8'
    )
    app = SimpleNamespace(
        csv_dir=csv_dir,
        operator_settings=lambda: {'execution_queue_enabled': 'true'},
    )
    rec = {
        'recommendation_id': 'abc', 'recommendation_mode': 'ARMED', 'action': 'IN',
        'chain_id': 56, 'chain_slug': 'bsc', 'wallet': '0x' + '1'*40,
        'behaviour': 'TRIANGULAR_MULTI_HOP_ARBITRAGE', 'route_id': 'r1',
        'recommended_input_base': 0.05, 'conservative_net_profit_base': 0.002,
    }
    first = queue_armed_recommendations(app, [rec])
    second = queue_armed_recommendations(app, [rec])
    assert first['added'] == 1
    assert second['added'] == 0
    assert queue_summary(csv_dir)['pending'] == 1


def test_shadow_does_not_queue(tmp_path: Path):
    csv_dir = tmp_path / 'CSVbot'; csv_dir.mkdir()
    app = SimpleNamespace(csv_dir=csv_dir, operator_settings=lambda: {'execution_queue_enabled': 'true'})
    rec = {'recommendation_id':'x','recommendation_mode':'SHADOW','action':'IN'}
    result = queue_armed_recommendations(app, [rec])
    assert result['added'] == 0
