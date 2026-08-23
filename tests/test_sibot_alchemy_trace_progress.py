import time

from learnerbot import sibot_alchemy_trace_progress_patch as patch


class Row(dict):
    pass


def test_progress_candidate_respects_candidate_priority_and_cooldown():
    now = 10_000
    rows = [
        Row(wallet="b", fetched_at=now - 100),
        Row(wallet="a", fetched_at=now - 100),
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


def test_trace_cache_tracks_missing_without_marking_partial_complete():
    chain_id = 56
    wallet = "0xabc"
    key = (chain_id, wallet)
    with patch._CACHE_LOCK:
        patch._TRACE_CACHE.pop(key, None)

    cache, missing = patch._cached_internal_rows(chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert cache == {}
    assert missing == ["tx1", "tx2", "tx3"]

    patch._merge_cache(chain_id, wallet, {"tx1": [], "tx2": [{"hash": "tx2"}]})
    cache, missing = patch._cached_internal_rows(chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert set(cache) == {"tx1", "tx2"}
    assert missing == ["tx3"]

    patch._merge_cache(chain_id, wallet, {"tx3": []})
    cache, missing = patch._cached_internal_rows(chain_id, wallet, ["tx1", "tx2", "tx3"])
    assert missing == []


def test_progress_retry_interval_is_bounded():
    assert 1 <= patch._PROGRESS_RETRY_SECONDS <= 60
    assert 1 <= patch._TRACE_BATCH_SIZE <= 10
