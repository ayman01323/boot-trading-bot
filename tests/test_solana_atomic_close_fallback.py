from __future__ import annotations

from types import SimpleNamespace

import pytest

from learnerbot import solana_atomic_close_fallback_patch as fallback
from learnerbot import solana_execution_efficiency_patch as eff


class _Executor:
    app = SimpleNamespace()
    telegram_id = "123"
    address = "11111111111111111111111111111111"

    def token_balance_raw(self, mint):
        return 42

    def _headers(self, json_body=False):
        # Match the SolanaLiveExecutor interface used by the atomic Jupiter build.
        # The test only inspects request parameters, so no auth headers are needed.
        return {}


def test_unproven_legacy_full_exit_falls_back_to_capped_managed_sell(monkeypatch):
    calls = []
    monkeypatch.setattr(eff, "_cfg", lambda app: {"live_require_atomic_full_close": "true"})
    monkeypatch.setattr(
        eff,
        "_atomic_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(eff._exec.SolanaLiveError("not tracked")),
    )
    monkeypatch.setattr(fallback, "_fallback_guard_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(eff, "_PREV_SELL", lambda self, mint, amount: calls.append((mint, amount)) or {"managed": True})

    result = fallback.sell_with_atomic_or_capped_legacy_fallback(_Executor(), "mint", 42)
    assert result == {"managed": True}
    assert calls == [("mint", 42)]


def test_eligible_full_exit_uses_atomic_path(monkeypatch):
    candidate = ({"position_id": "p1"}, {"account_pubkey": "a"}, {"pubkey": "a"})
    monkeypatch.setattr(eff, "_cfg", lambda app: {"live_require_atomic_full_close": "true"})
    monkeypatch.setattr(eff, "_atomic_candidate", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(eff, "atomic_full_sell", lambda self, mint, amount, found: {"atomic": found is candidate})
    monkeypatch.setattr(
        eff,
        "_PREV_SELL",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("managed fallback must not run")),
    )

    assert fallback.sell_with_atomic_or_capped_legacy_fallback(_Executor(), "mint", 42) == {"atomic": True}


def test_atomic_execution_failure_is_not_retried_as_managed_sell(monkeypatch):
    candidate = ({"position_id": "p1"}, {"account_pubkey": "a"}, {"pubkey": "a"})
    monkeypatch.setattr(eff, "_cfg", lambda app: {"live_require_atomic_full_close": "true"})
    monkeypatch.setattr(eff, "_atomic_candidate", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(
        eff,
        "atomic_full_sell",
        lambda *args, **kwargs: (_ for _ in ()).throw(eff._exec.SolanaLiveError("simulation failed")),
    )
    monkeypatch.setattr(
        eff,
        "_PREV_SELL",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not retry managed route after atomic start")),
    )

    with pytest.raises(eff._exec.SolanaLiveError, match="simulation failed"):
        fallback.sell_with_atomic_or_capped_legacy_fallback(_Executor(), "mint", 42)


def test_atomic_build_explicitly_excludes_rfq(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"outAmount": "1", "swapInstruction": {"programId": "x"}}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(params or {})
        return _Response()

    monkeypatch.setattr(eff.requests, "get", fake_get)
    fallback.build_atomic_swap_excluding_rfq(_Executor(), "mint", 42, 50)
    assert captured["excludeRouters"] == "jupiterz"
