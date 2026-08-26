from __future__ import annotations

import learnerbot.sibot_alchemy_context_rate_patch as patch


def _erc20(tx_hash: str, wallet: str, other: str, *, outbound=False):
    return {
        "category": "erc20",
        "hash": tx_hash,
        "from": wallet if outbound else other,
        "to": other if outbound else wallet,
        "rawContract": {"value": "0x1", "decimal": "0x12", "address": "0x" + "3" * 40},
        "metadata": {"blockTimestamp": "2026-08-26T10:00:00Z"},
        "blockNum": "0x123",
    }


def test_candidate_hashes_keep_only_wallet_erc20_movement():
    wallet = "0x" + "1" * 40
    other = "0x" + "2" * 40
    rows = [
        _erc20("0xa", wallet, other),
        {**_erc20("0xb", wallet, other), "from": other, "to": "0x" + "4" * 40},
        {"category": "external", "hash": "0xc", "from": wallet, "to": other, "value": 1},
        _erc20("0xa", wallet, other),
    ]
    assert patch._candidate_hashes(rows, wallet) == ["0xa"]


def test_context_uses_small_batches_and_skips_unrelated_hashes(monkeypatch):
    wallet = "0x" + "1" * 40
    other = "0x" + "2" * 40
    rows = [_erc20(f"0x{i:064x}", wallet, other, outbound=bool(i % 2)) for i in range(25)]
    rows += [
        {
            "category": "external",
            "hash": f"0xdead{i}",
            "from": wallet,
            "to": other,
            "value": 1,
            "metadata": {"blockTimestamp": "2026-08-26T10:00:00Z"},
        }
        for i in range(10)
    ]
    calls = []

    def fake_batch(url, method, params_rows, timeout=45):
        calls.append((method, len(params_rows)))
        if method == "eth_getTransactionByHash":
            return [
                {"from": wallet, "to": other, "value": "0x0", "gasPrice": "0x1"}
                for _ in params_rows
            ]
        if method == "eth_getTransactionReceipt":
            return [
                {"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1"}
                for _ in params_rows
            ]
        raise AssertionError(method)

    monkeypatch.setattr(patch._alchemy, "_batch_rpc", fake_batch)
    monkeypatch.setattr(patch.time, "sleep", lambda _seconds: None)

    normal, outgoing, _ts = patch.tx_context_swap_relevant("http://unused", rows, wallet)

    assert len(normal) == len(outgoing) == 25
    assert calls == [
        ("eth_getTransactionByHash", 20),
        ("eth_getTransactionReceipt", 20),
        ("eth_getTransactionByHash", 5),
        ("eth_getTransactionReceipt", 5),
    ]
    assert all(len(out) > 10 for out in outgoing)
