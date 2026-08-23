from __future__ import annotations


def test_synced_runtime_bridge_fills_only_missing_provider_secrets(monkeypatch) -> None:
    from learnerbot import ai_runtime_secret_fallback_patch as patch

    monkeypatch.setattr(patch, '_ORIGINAL_RUNTIME_ENV', lambda: {'GEMINI_API_KEY': 'base'})
    monkeypatch.setattr(
        patch,
        'dotenv_values',
        lambda path: {'GEMINI_API_KEY': 'fresh', 'OPENAI_API_KEY': 'openai'}
        if str(path) == '/var/tmp/ai_council_runtime.env'
        else {},
    )
    monkeypatch.setattr(patch._base, '_SECRET_KEYS', {'GEMINI_API_KEY', 'OPENAI_API_KEY'})

    env = patch.runtime_env_with_synced_fallback()
    assert env['GEMINI_API_KEY'] == 'base'
    assert env['OPENAI_API_KEY'] == 'openai'


def test_synced_runtime_bridge_fails_open(monkeypatch) -> None:
    from learnerbot import ai_runtime_secret_fallback_patch as patch

    monkeypatch.setattr(patch, '_ORIGINAL_RUNTIME_ENV', lambda: {'GEMINI_API_KEY': 'base'})

    def broken(_path):
        raise OSError('unavailable')

    monkeypatch.setattr(patch, 'dotenv_values', broken)
    assert patch.runtime_env_with_synced_fallback()['GEMINI_API_KEY'] == 'base'
