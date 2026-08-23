import time

from learnerbot import sibot_alchemy_trace_progress_patch as patch


class Row(dict):
    pass


def test_progress_candidate_respects_candidate_priority_and_cooldown():
    now = 10_000
    rows = [
        Row(wallet="b", fetched_at=now - 500),
        Row(wallet="a", fetched_at=now - 500),
    ]
    assert patch._progress_candidate(["a", "b"], rows, now) == "a"
    assert patch._progress_candidate(["a"], [Row(wallet="a", fetched_at=now)], now) is None


def test_trace_chunk_batches_and_records_empty_trace_as_completed(monkeypatch):
    seen = {}

    def fake_post(url, payload, timeout, label):
        seen["count"] = len(payload)
        seen["label"] = label
        return [
            {"jsonrpc": "2.0", "id": 1, "result": {"from": "0x1", "to": "0x2", "value": "0x0"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"from": "0x1", "to": "0x2", "value": "0x0"}},
        ]

    monkeypatch.setattr(patch._alchemy, "_post_json", fake_post)
    out = patch._trace_chunk("https://example.invalid", "0xabc", ["0x1", "0x2"], {})
    assert seen == {"count": 2, "label": "debug_traceTransaction"}
    assert out == {"0x1": [], "0x2": []}


def test_trace_cache_tracks_missing_without_marking_partial_complete(monkeypatch):
    chain_id = 56
    wallet = "0xabc"
    key = (chain_id, wallet)
    with patch._CACHE_LOCK:
        patch._TRACE_CACHE.pop(key, None)

    monkeypatch.setattr(patch, "_load_persistent_trace_cache", lambda *_args: {})
    monkeypatch.setattr(patch, "_save_persistent_trace_cache", lambda *_args: None)

    cache, missing = patch._cached_internal_rows(object(), chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert cache == {}
    assert missing == ["tx1", "tx2", "tx3"]

    patch._merge_cache(object(), chain_id, wallet, {"tx1": [], "tx2": [{"hash": "tx2"}]})
    cache, missing = patch._cached_internal_rows(object(), chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert set(cache) == {"tx1", "tx2"}
    assert missing == ["tx3"]

    patch._merge_cache(object(), chain_id, wallet, {"tx3": []})
    cache, missing = patch._cached_internal_rows(object(), chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert missing == []


def test_context_cache_reuses_expensive_history_context_until_ttl():
    chain_id = 42161
    wallet = "0xdef"
    patch._clear_context(chain_id, wallet)
    row = patch._set_context(chain_id, wallet, {"normal": [{"hash": "0x1"}]}, 180)
    assert row["normal"] == [{"hash": "0x1"}]
    assert patch._get_context(chain_id, wallet)["normal"] == [{"hash": "0x1"}]
    patch._clear_context(chain_id, wallet)
    assert patch._get_context(chain_id, wallet) is None


def test_progress_retry_interval_is_cost_controlled_but_bounded():
    assert 120 <= patch._PROGRESS_RETRY_SECONDS <= 900
    assert 120 <= patch._CONTEXT_TTL_SECONDS <= 3600
    assert 1 <= patch._TRACE_BATCH_SIZE <= 10
