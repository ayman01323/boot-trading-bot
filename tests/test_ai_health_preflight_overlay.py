from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_stale_review_overlay_requires_fresh_working_preflight() -> None:
    body = _text("learnerbot/ai_health_preflight_overlay_patch.py")
    assert '"gpt": "openai"' in body
    assert '"claude": "anthropic"' in body
    assert '"deepseek": "deepseek"' in body
    assert '"grok": "xai"' in body
    assert "_PREFLIGHT_MAX_AGE_SECONDS = 20 * 60" in body
    assert "_STALE_REVIEW_SECONDS = 30 * 60" in body
    assert 'if review_age < _STALE_REVIEW_SECONDS:' in body
    assert 'if str(live.get("state") or "").upper() != "WORKING":' in body


def test_overlay_softens_only_to_refreshing_not_working() -> None:
    body = _text("learnerbot/ai_health_preflight_overlay_patch.py")
    assert 'if str(detail.get("state") or "").upper() != "NOT_WORKING":' in body
    assert 'detail["state"] = "WAITING"' in body
    assert 'detail["provider_preflight"] = "WORKING"' in body
    assert 'return "🟡", "Refreshing"' in body
    assert 'detail["state"] = "WORKING"' not in body


def test_overlay_updates_all_health_entry_points() -> None:
    body = _text("learnerbot/ai_health_preflight_overlay_patch.py")
    for expected in (
        "_health6._engineering_health = engineering_health",
        "_health6._strategy_health = strategy_health",
        "_warning._engineering_health = engineering_health",
        "_warning._strategy_health = strategy_health",
        "_compact.classify_health = classify_health",
    ):
        assert expected in body


def test_overlay_is_loaded_after_existing_telegram_patches() -> None:
    loader = _text("learnerbot/telegram_auto_updates_category_toggle_patch.py")
    assert "from . import ai_health_preflight_overlay_patch" in loader
    assert "does not alter trading, risk, capital, wallets, signing or deployment" in loader
