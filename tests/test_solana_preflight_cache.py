import time
from decimal import Decimal

from learnerbot import solana_preflight_cache_patch as cache


def _event():
    return {
        "signature": "sig",
        "mint": "mint",
        "token_amount_raw": "100",
        "sol_amount": "0.1",
        "event_ts": int(time.time()),
    }


def test_same_fresh_signal_preflight_is_shared(monkeypatch):
    cache._CACHE.clear()
    calls = []

    def previous(app, event, allocation, cfg):
        calls.append(1)
        return True, "PASS", {"out_raw": 100}

    monkeypatch.setattr(cache, "_PREV_VALIDATE", previous)
    cfg = {
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
    }
    first = cache.validate_entry_cached(object(), _event(), Decimal("0.0005"), cfg)
    second = cache.validate_entry_cached(object(), _event(), Decimal("0.0005"), cfg)
    assert first == second
    assert len(calls) == 1


def test_stale_signal_is_rejected_even_if_matching_result_was_cached(monkeypatch):
    cache._CACHE.clear()
    event = _event()
    cfg = {
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
    }
    monkeypatch.setattr(cache, "_PREV_VALIDATE", lambda *args: (True, "PASS", {"out_raw": 100}))
    assert cache.validate_entry_cached(object(), event, Decimal("0.0005"), cfg)[0] is True
    event["event_ts"] = int(time.time()) - 31
    ok, reason, _ = cache.validate_entry_cached(object(), event, Decimal("0.0005"), cfg)
    assert ok is False
    assert "stale signal" in reason


def test_exception_is_not_cached(monkeypatch):
    cache._CACHE.clear()
    calls = []

    def previous(app, event, allocation, cfg):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary quote failure")
        return True, "PASS", {}

    monkeypatch.setattr(cache, "_PREV_VALIDATE", previous)
    cfg = {"max_signal_age_seconds": "30"}
    try:
        cache.validate_entry_cached(object(), _event(), Decimal("0.0005"), cfg)
    except RuntimeError:
        pass
    assert cache.validate_entry_cached(object(), _event(), Decimal("0.0005"), cfg)[0] is True
    assert len(calls) == 2
