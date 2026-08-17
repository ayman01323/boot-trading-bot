from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_capital_dashboard_uses_background_thread():
    s=(ROOT/'learnerbot/telegram_dashboard_patch.py').read_text()
    assert 'threading.Thread' in s
    assert 'target=_dashboard_worker' in s
    assert '_REFRESHING' in s
    assert 'Already refreshing' in s


def test_capital_buttons_and_commands_present():
    s=(ROOT/'learnerbot/telegram_dashboard_patch.py').read_text()
    for value in ['menu:adminwallets','menu:capital','/adminwallets','/capital']:
        assert value in s


def test_dashboard_worker_sends_result_and_releases_lock():
    s=(ROOT/'learnerbot/telegram_dashboard_patch.py').read_text()
    worker=s[s.index('def _dashboard_worker'):s.index('def _start_dashboard_refresh')]
    assert '_ui._send' in worker
    assert '_REFRESHING.discard(key)' in worker
