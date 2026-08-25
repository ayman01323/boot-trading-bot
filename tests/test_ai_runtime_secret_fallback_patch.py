from __future__ import annotations

from pathlib import Path


def test_synced_runtime_bridge_overrides_stale_provider_secrets(monkeypatch) -> None:
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
    assert env['GEMINI_API_KEY'] == 'fresh'
    assert env['OPENAI_API_KEY'] == 'openai'


def test_synced_runtime_bridge_overrides_stale_copilot_and_gemini(monkeypatch) -> None:
    from learnerbot import ai_runtime_secret_fallback_patch as patch

    monkeypatch.setattr(
        patch,
        '_ORIGINAL_RUNTIME_ENV',
        lambda: {
            'COPILOT_GITHUB_TOKEN': 'stale-primary',
            'GEMINI_API_KEY': 'base-gemini',
        },
    )
    monkeypatch.setattr(
        patch,
        'dotenv_values',
        lambda path: {
            'COPILOT_GITHUB_TOKEN': 'validated-primary',
            'GEMINI_API_KEY': 'fresh-gemini',
        }
        if str(path) == '/var/tmp/ai_council_runtime.env'
        else {},
    )
    monkeypatch.setattr(
        patch._base,
        '_SECRET_KEYS',
        {'COPILOT_GITHUB_TOKEN', 'GEMINI_API_KEY'},
    )

    env = patch.runtime_env_with_synced_fallback()
    assert env['COPILOT_GITHUB_TOKEN'] == 'validated-primary'
    assert env['GEMINI_API_KEY'] == 'fresh-gemini'


def test_synced_runtime_bridge_fails_open(monkeypatch) -> None:
    from learnerbot import ai_runtime_secret_fallback_patch as patch

    monkeypatch.setattr(patch, '_ORIGINAL_RUNTIME_ENV', lambda: {'GEMINI_API_KEY': 'base'})

    def broken(_path):
        raise OSError('unavailable')

    monkeypatch.setattr(patch, 'dotenv_values', broken)
    assert patch.runtime_env_with_synced_fallback()['GEMINI_API_KEY'] == 'base'


def test_install_redirects_base_loader_to_authoritative_synced_runtime(monkeypatch, tmp_path) -> None:
    from learnerbot import ai_runtime_secret_fallback_patch as patch

    synced = tmp_path / 'authoritative-runtime.env'
    monkeypatch.setattr(patch, '_SYNCED_RUNTIME_ENV', synced)
    monkeypatch.setattr(patch._base, '_RUNTIME_ENV', Path('/legacy/stale-runtime.env'))
    monkeypatch.setattr(patch._base, '_runtime_env', patch._ORIGINAL_RUNTIME_ENV)

    patch.install()

    assert patch._base._RUNTIME_ENV == synced
    assert patch._base._runtime_env is patch.runtime_env_with_synced_fallback
