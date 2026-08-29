from __future__ import annotations

import pytest

from learnerbot import solana_manual_force_exit_reconcile_patch as patch


def _position(pid="p1"):
    return {
        "position_id": pid,
        "telegram_id": "123",
        "status": "OPEN",
        "mint": "TokenMint111111111111111111111111111111111",
    }


def test_automatic_close_still_delegates_to_existing_guard(monkeypatch):
    calls = []

    def guarded(app, tid, position, fraction, reason):
        calls.append((tid, position["position_id"], fraction, reason))
        return {"guarded": True}

    def forbidden_inner(*args, **kwargs):
        raise AssertionError("automatic path must not bypass the circuit guard")

    monkeypatch.setattr(patch, "_BASE_GUARDED_CLOSE", guarded)
    monkeypatch.setattr(patch._exit, "_PREV_CLOSE", forbidden_inner)

    assert patch._MANUAL_RETRY_POSITION.get() == ""
    result = patch._manual_context_close(None, "123", _position(), 1, "AUTO")

    assert result == {"guarded": True}
    assert calls == [("123", "p1", 1, "AUTO")]


def test_matching_manual_context_bypasses_only_stale_guard(monkeypatch):
    calls = []

    def forbidden_guard(*args, **kwargs):
        raise AssertionError("proved manual retry should bypass only the stale guard")

    def inner(app, tid, position, fraction, reason):
        calls.append((tid, position["position_id"], fraction, reason))
        return {"closed": True, "signature": "new-sig"}

    monkeypatch.setattr(patch, "_BASE_GUARDED_CLOSE", forbidden_guard)
    monkeypatch.setattr(patch._exit, "_PREV_CLOSE", inner)

    token = patch._MANUAL_RETRY_POSITION.set("p1")
    try:
        result = patch._manual_context_close(
            None, "123", _position(), 1, "SOLANA_MANUAL_FORCE_EXIT"
        )
    finally:
        patch._MANUAL_RETRY_POSITION.reset(token)

    assert result["closed"] is True
    assert calls == [("123", "p1", 1, "SOLANA_MANUAL_FORCE_EXIT")]


def test_blocked_circuit_without_visible_chain_proof_never_retries(monkeypatch):
    retried = []
    monkeypatch.setattr(
        patch,
        "_load_owned_open_position",
        lambda app, tid, pid: _position(pid),
    )
    monkeypatch.setattr(
        patch._exit,
        "circuit_row",
        lambda app, pid: {
            "status": "RECONCILING",
            "tx_signature": "old-sig",
        },
    )
    monkeypatch.setattr(
        patch._binding,
        "_resolve_executor",
        lambda app, tid, position: (object(), "wallet"),
    )
    monkeypatch.setattr(
        patch._exit,
        "_chain_sell_evidence",
        lambda app, executor, signature, mint: {
            "visible": False,
            "error": "RPC lag",
        },
    )
    monkeypatch.setattr(
        patch,
        "_BASE_FORCE_CLOSE",
        lambda *args, **kwargs: retried.append(True),
    )

    with pytest.raises(ValueError, match="not conclusively visible"):
        patch._reconcile_first_force_close(None, "123", "p1")

    assert retried == []
    assert "p1" not in patch._INFLIGHT_POSITIONS


def test_chain_proven_no_sale_allows_one_scoped_manual_retry(monkeypatch):
    marks = []
    observed_context = []
    monkeypatch.setattr(
        patch,
        "_load_owned_open_position",
        lambda app, tid, pid: _position(pid),
    )
    monkeypatch.setattr(
        patch._exit,
        "circuit_row",
        lambda app, pid: {
            "status": "LANDED_INVALID",
            "tx_signature": "old-sig",
        },
    )
    monkeypatch.setattr(
        patch._binding,
        "_resolve_executor",
        lambda app, tid, position: (object(), "wallet"),
    )
    monkeypatch.setattr(
        patch._exit,
        "_chain_sell_evidence",
        lambda app, executor, signature, mint: {
            "visible": True,
            "tx_ok": True,
            "token_delta_raw": 0,
        },
    )

    def force_once(app, tid, pid):
        observed_context.append(patch._MANUAL_RETRY_POSITION.get())
        return {"closed": True, "signature": "new-sig"}

    monkeypatch.setattr(patch, "_BASE_FORCE_CLOSE", force_once)
    monkeypatch.setattr(
        patch._exit,
        "_mark_circuit",
        lambda app, pid, status, error="": marks.append((pid, status, error)),
    )

    result = patch._reconcile_first_force_close(None, "123", "p1")

    assert observed_context == ["p1"]
    assert patch._MANUAL_RETRY_POSITION.get() == ""
    assert result["manual_force_reconcile_first"] is True
    assert result["manual_retry_prior_signature"] == "old-sig"
    assert marks and marks[-1][0:2] == ("p1", "RECONCILED")
    assert "p1" not in patch._INFLIGHT_POSITIONS


def test_blocked_circuit_without_signature_is_fail_closed(monkeypatch):
    retried = []
    monkeypatch.setattr(
        patch,
        "_load_owned_open_position",
        lambda app, tid, pid: _position(pid),
    )
    monkeypatch.setattr(
        patch._exit,
        "circuit_row",
        lambda app, pid: {"status": "LANDED_INVALID", "tx_signature": ""},
    )
    monkeypatch.setattr(
        patch,
        "_BASE_FORCE_CLOSE",
        lambda *args, **kwargs: retried.append(True),
    )

    with pytest.raises(ValueError, match="has no transaction signature"):
        patch._reconcile_first_force_close(None, "123", "p1")

    assert retried == []
