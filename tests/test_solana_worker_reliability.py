from types import SimpleNamespace

import pytest

from learnerbot import solana_sibot as sol
from learnerbot import solana_worker_reliability_patch as reliability


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def test_failed_block_is_not_skipped_forever(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        sol._set_state(conn, "last_discovery_slot", 4)

    def fake_rpc(app, method, params):
        if method == "getSlot":
            return 10
        if method == "getBlock":
            slot = int(params[0])
            if slot == 5:
                return {"blockTime": 1, "transactions": []}
            if slot == 6:
                raise RuntimeError("temporary RPC failure")
            raise AssertionError(slot)
        raise AssertionError(method)

    monkeypatch.setattr(sol, "_rpc", fake_rpc)
    found = reliability.discover_recent_blocks_reliable(app)
    assert found == 0
    with sol.connect(app) as conn:
        assert int(sol._state(conn, "last_discovery_slot", 0)) == 5


def test_rpc_retry_retries_transient_failure(monkeypatch, tmp_path):
    app = _app(tmp_path)
    calls = []

    def previous(app, method, params):
        calls.append(method)
        if len(calls) < 3:
            raise RuntimeError("429 rate limit")
        return {"value": 7}

    monkeypatch.setattr(reliability, "_PREV_RPC", previous)
    monkeypatch.setattr(reliability.time, "sleep", lambda seconds: None)
    result = reliability.rpc_with_retry(app, "getBalance", [])
    assert result == {"value": 7}
    assert len(calls) == 3


def test_rpc_retry_does_not_repeat_non_transient_error(monkeypatch, tmp_path):
    app = _app(tmp_path)
    calls = []

    def previous(app, method, params):
        calls.append(method)
        raise ValueError("invalid parameters")

    monkeypatch.setattr(reliability, "_PREV_RPC", previous)
    with pytest.raises(ValueError, match="invalid parameters"):
        reliability.rpc_with_retry(app, "bad", [])
    assert len(calls) == 1
