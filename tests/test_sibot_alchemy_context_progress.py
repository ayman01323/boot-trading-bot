from __future__ import annotations

import pytest

from learnerbot import sibot_alchemy_context_progress_patch as patch


def _transfers(wallet: str, count: int) -> list[dict]:
    return [
        {
            "hash": f"0x{idx:064x}",
            "blockNum": hex(1000 + idx),
            "metadata": {"blockTimestamp": "2026-08-23T10:00:00Z"},
        }
        for idx in range(count)
    ]


def test_context_reconstruction_yields_before_one_wallet_can_monopolise(monkeypatch):
    wallet = "0x" + "11" * 20
    patch._CONTEXT_CACHE.clear()
    calls: list[tuple[str, int]] = []

    def fake_batch(url, method, params_rows, timeout=45):
        calls.append((method, len(params_rows)))
        if method == "eth_getTransactionByHash":
            return [
                {
                    "from": wallet,
                    "to": "0x" + "22" * 20,
                    "value": "0x1",
                    "gasPrice": "0x2",
                }
                for _ in params_rows
            ]
        if method == "eth_getTransactionReceipt":
            return [
                {"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x2"}
                for _ in params_rows
            ]
        raise AssertionError(method)

    monkeypatch.setattr(patch._alchemy, "_batch_rpc", fake_batch)
    transfers = _transfers(wallet, 35)

    with pytest.raises(RuntimeError, match=r"AlchemyHistoryProgress: context progress pending 30/35"):
        patch._tx_context("https://example.invalid/v2/private", transfers, wallet)

    normal, outgoing, _ = patch._tx_context("https://example.invalid/v2/private", transfers, wallet)
    assert len(normal) == 35
    assert len(outgoing) == 35
    assert max(size for _, size in calls) <= patch._RPC_BATCH_SIZE
    # 35 hashes need four transaction batches + four receipt batches total,
    # spread across two worker cycles rather than one unbounded call.
    assert sum(1 for method, _ in calls if method == "eth_getTransactionByHash") == 4
    assert sum(1 for method, _ in calls if method == "eth_getTransactionReceipt") == 4


def test_context_progress_prefers_ranked_candidate_after_short_cooldown():
    rows = [
        {"wallet": "0xaaa", "fetched_at": 100},
        {"wallet": "0xbbb", "fetched_at": 100},
    ]
    assert patch._progress_candidate(["0xbbb", "0xaaa"], rows, 109) == "0xbbb"
    assert patch._progress_candidate(["0xbbb", "0xaaa"], rows, 105) is None


def test_runtime_installs_context_progress_before_legacy_sweep():
    text = patch.__file__
    assert text
    from pathlib import Path

    runtime = (Path(__file__).resolve().parents[1] / "learnerbot" / "trade_blocker_alchemy_history_patch.py").read_text(encoding="utf-8")
    assert runtime.index("sibot_alchemy_context_progress_patch") < runtime.index("sibot_legacy_error_sweep_patch")
