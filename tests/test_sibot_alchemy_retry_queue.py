from types import SimpleNamespace

from learnerbot import sibot_alchemy_retry_queue_patch as patch


class Row(dict):
    def __getattr__(self, name):
        return self[name]


def test_legacy_etherscan_candidate_retries_immediately():
    now = 1_000_000
    candidates = ["0xtop", "0xnext"]
    rows = [
        Row(wallet="0xtop", fetched_at=now, error="RuntimeError: ETHERSCAN_API_KEY is not configured"),
        Row(wallet="0xnext", fetched_at=now - 999, error=""),
    ]
    assert patch._priority_retry_candidate(candidates, rows, now) == "0xtop"


def test_invalid_etherscan_key_row_is_legacy_and_retries_immediately():
    now = 1_000_000
    rows = [
        Row(wallet="0xtop", fetched_at=now, error="RuntimeError: Etherscan txlist: NOTOK Invalid API Key (#err2)")
    ]
    assert patch._legacy_etherscan_error(rows[0]["error"]) is True
    assert patch._priority_retry_candidate(["0xtop"], rows, now) == "0xtop"


def test_unsupported_free_etherscan_chain_row_is_legacy_and_retries_immediately():
    now = 1_000_000
    rows = [
        Row(
            wallet="0xtop",
            fetched_at=now,
            error="RuntimeError: Etherscan txlist: NOTOK Free API access is not supported for this chain. Please upgrade your api plan",
        )
    ]
    assert patch._priority_retry_candidate(["0xtop"], rows, now) == "0xtop"


def test_alchemy_429_candidate_retries_after_short_cooldown():
    now = 1_000_000
    rows = [
        Row(
            wallet="0xtop",
            fetched_at=now - patch._TRANSIENT_RETRY_COOLDOWN_SECONDS - 1,
            error="AlchemyHistoryError: RuntimeError: Alchemy eth_getTransactionReceipt: HTTP 429; retries exhausted",
        )
    ]
    assert patch._priority_retry_candidate(["0xtop"], rows, now) == "0xtop"


def test_fresh_alchemy_429_respects_cooldown():
    now = 1_000_000
    rows = [
        Row(
            wallet="0xtop",
            fetched_at=now - patch._TRANSIENT_RETRY_COOLDOWN_SECONDS + 1,
            error="AlchemyHistoryError: RuntimeError: Alchemy alchemy_getAssetTransfers: RPC 429; retries exhausted",
        )
    ]
    assert patch._priority_retry_candidate(["0xtop"], rows, now) is None


def test_non_rate_alchemy_error_is_not_forced_into_short_retry_loop():
    now = 1_000_000
    rows = [
        Row(
            wallet="0xtop",
            fetched_at=now - 9999,
            error="AlchemyHistoryError: RuntimeError: Alchemy debug_traceTransaction: HTTP 403",
        )
    ]
    assert patch._priority_retry_candidate(["0xtop"], rows, now) is None


def test_refresh_is_serialised(monkeypatch):
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    monkeypatch.setattr(patch, "_SERIAL_HISTORY_LOCK", FakeLock())
    monkeypatch.setattr(
        patch,
        "_PREV_REFRESH_WALLET_HISTORY",
        lambda app, chain, wallet: events.append((chain.chain_id, wallet)) or {"ok": True},
    )
    result = patch.refresh_wallet_history(SimpleNamespace(), SimpleNamespace(chain_id=137), "0xabc")
    assert result == {"ok": True}
    assert events == ["enter", (137, "0xabc"), "exit"]
