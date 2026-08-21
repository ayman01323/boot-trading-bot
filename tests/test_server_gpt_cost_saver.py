from types import SimpleNamespace

from learnerbot import profit_control_loop_patch as control
from learnerbot import server_gpt_cost_saver_patch as saver


def test_legacy_server_gpt_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv('SERVER_HOURLY_GPT_ENABLED', raising=False)
    app = SimpleNamespace(general=lambda: {})
    assert saver.server_hourly_gpt_enabled(app) is False


def test_legacy_server_gpt_can_be_explicitly_reenabled(monkeypatch):
    monkeypatch.setenv('SERVER_HOURLY_GPT_ENABLED', 'true')
    app = SimpleNamespace(general=lambda: {})
    assert saver.server_hourly_gpt_enabled(app) is True


def test_cost_saver_skips_paid_inner_review_but_keeps_deterministic_control(monkeypatch):
    monkeypatch.setattr(saver, 'server_hourly_gpt_enabled', lambda app: False)
    monkeypatch.setattr(
        control,
        '_server_gpt_original_review',
        lambda app, zip_path: (_ for _ in ()).throw(AssertionError('paid GPT must not be called')),
    )
    monkeypatch.setattr(
        control,
        'run_profit_control_loop',
        lambda app, result: {'active_profile': 'BASELINE', 'live_armed_state_changed': False},
    )
    result = control.run_hourly_gpt_review_with_control(object(), 'audit.zip')
    assert result['ok'] is True
    assert result['skipped'] is True
    assert result['cost_control'] == 'REDUNDANT_SERVER_GPT_DISABLED'
    assert result['control_loop']['active_profile'] == 'BASELINE'
    assert result['control_loop']['live_armed_state_changed'] is False


def test_cost_saver_calls_original_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(saver, 'server_hourly_gpt_enabled', lambda app: True)
    monkeypatch.setattr(control, '_server_gpt_original_review', lambda app, zip_path: {'ok': True, 'review': {'status': 'HEALTHY'}})
    result = saver._cost_controlled_original_review(object(), 'audit.zip')
    assert result['ok'] is True
    assert result['review']['status'] == 'HEALTHY'


def test_skipped_server_review_sends_no_extra_gpt_telegram_message(monkeypatch):
    called = []
    monkeypatch.setattr(saver._worker, '_server_gpt_original_send_message', lambda *args, **kwargs: called.append(True))
    saver._send_gpt_review_message_cost_aware(object(), '1', saver.skipped_server_gpt_result())
    assert called == []


def test_cost_saver_loads_before_final_runtime_invariant():
    text = open('learnerbot/__main__.py', encoding='utf-8').read()
    assert text.index('server_gpt_cost_saver_patch') < text.index('trading_runtime_invariant_patch')
