from __future__ import annotations

from learnerbot import sibot1_gemini_telegram_dedicated_patch as dedicated


def test_prepare_long_poll_clears_webhook_and_preserves_pending(monkeypatch):
    infos = iter([
        {"url": "https://example.invalid/old-hook"},
        {"url": ""},
    ])
    calls = []

    monkeypatch.setattr(dedicated._tg, "get_webhook_info", lambda token: next(infos))
    monkeypatch.setattr(
        dedicated._tg,
        "_json",
        lambda method, token, **kwargs: calls.append((method, kwargs)) or True,
    )

    dedicated._prepare_long_poll("redacted-test-token")

    assert calls == [
        (
            "deleteWebhook",
            {"payload": {"drop_pending_updates": False}, "timeout": 15},
        )
    ]


def test_prepare_long_poll_fails_if_webhook_remains(monkeypatch):
    monkeypatch.setattr(
        dedicated._tg,
        "get_webhook_info",
        lambda token: {"url": "https://example.invalid/still-set"},
    )
    monkeypatch.setattr(dedicated._tg, "_json", lambda *args, **kwargs: True)

    try:
        dedicated._prepare_long_poll("redacted-test-token")
    except RuntimeError as exc:
        assert "remained configured" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when webhook remains configured")
