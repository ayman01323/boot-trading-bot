from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_telegram_safety_patch_loaded_before_dashboard():
    s=(ROOT/'learnerbot/__main__.py').read_text()
    assert s.index('telegram_safety_patch') < s.index('telegram_dashboard_patch')


def test_callback_ack_is_best_effort():
    s=(ROOT/'learnerbot/telegram_safety_patch.py').read_text()
    assert 'def _safe_answer_callback_query' in s
    assert 'except Exception:' in s
    assert '_tg.answer_callback_query=_safe_answer_callback_query' in s


def test_transport_error_does_not_include_token_or_request_url():
    s=(ROOT/'learnerbot/telegram_safety_patch.py').read_text()
    assert 'Telegram API {method} failed' in s
    assert 'exc.response.url' not in s
    assert 'str(exc)' not in s
