from types import SimpleNamespace

from learnerbot import sibot_alchemy_internal_trace_patch as patch


WALLET = "0x0000000000000000000000000000000000000001"
ROUTER = "0x0000000000000000000000000000000000000002"
OTHER = "0x0000000000000000000000000000000000000003"
TOKEN = "0x0000000000000000000000000000000000000010"
TOKEN2 = "0x0000000000000000000000000000000000000011"


def _erc20(hash_, frm, to, token=TOKEN, value=1):
    return {
        "category": "erc20",
        "hash": hash_,
        "from": frm,
        "to": to,
        "rawContract": {"address": token, "value": hex(value), "decimal": "0x12"},
        "metadata": {"blockTimestamp": "2026-08-22T12:00:00Z"},
    }


def test_direct_flow_hashes_keep_only_single_net_wallet_token_direction():
    rows = [
        _erc20("0xaaa", ROUTER, WALLET, TOKEN, 5),
        _erc20("0xbbb", ROUTER, WALLET, TOKEN, 5),
        _erc20("0xbbb", ROUTER, WALLET, TOKEN2, 7),
        _erc20("0xccc", ROUTER, OTHER, TOKEN, 9),
        _erc20("0xddd", WALLET, ROUTER, TOKEN, 10),
        _erc20("0xddd", ROUTER, WALLET, TOKEN, 2),
    ]
    assert patch._direct_flow_hashes(rows, WALLET) == ["0xaaa", "0xddd"]
    kept = patch._direct_context_transfers(rows, WALLET)
    assert {row["hash"] for row in kept} == {"0xaaa", "0xddd"}


def test_direct_tx_context_fetches_receipts_only_for_wallet_configured_router(monkeypatch):
    rows = [
        {"hash": "0xaaa", "metadata": {"blockTimestamp": "2026-08-22T12:00:00Z"}},
        {"hash": "0xbbb", "metadata": {"blockTimestamp": "2026-08-22T12:00:01Z"}},
        {"hash": "0xccc", "metadata": {"blockTimestamp": "2026-08-22T12:00:02Z"}},
    ]
    calls = []

    def batch(url, method, params_rows, timeout=45):
        hashes = [params[0] for params in params_rows]
        calls.append((method, tuple(hashes)))
        if method == "eth_getTransactionByHash":
            mapping = {
                "0xaaa": {"from": WALLET, "to": ROUTER, "value": "0x0", "gasPrice": "0x1"},
                "0xbbb": {"from": OTHER, "to": ROUTER, "value": "0x0", "gasPrice": "0x1"},
                "0xccc": {"from": WALLET, "to": OTHER, "value": "0x0", "gasPrice": "0x1"},
            }
            return [mapping[h] for h in hashes]
        if method == "eth_getTransactionReceipt":
            assert hashes == ["0xaaa"]
            return [{"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1"}]
        raise AssertionError(method)

    monkeypatch.setattr(patch._alchemy, "_batch_rpc", batch)
    monkeypatch.setattr(patch.time, "sleep", lambda *_: None)
    normal, outgoing, _ts = patch._direct_tx_context("https://example", rows, WALLET, {ROUTER.lower()})
    assert [row["hash"] for row in normal] == ["0xaaa"]
    assert outgoing == ["0xaaa"]
    assert calls == [
        ("eth_getTransactionByHash", ("0xaaa", "0xbbb", "0xccc")),
        ("eth_getTransactionReceipt", ("0xaaa",)),
    ]


def test_trace_candidates_are_only_successful_direct_router_token_flows():
    normal = [
        {"hash": "0xaaa", "from": WALLET, "to": ROUTER, "isError": "0", "txreceipt_status": "1"},
        {"hash": "0xbbb", "from": WALLET, "to": ROUTER, "isError": "1", "txreceipt_status": "0"},
        {"hash": "0xccc", "from": WALLET, "to": OTHER, "isError": "0", "txreceipt_status": "1"},
        {"hash": "0xddd", "from": WALLET, "to": ROUTER, "isError": "0", "txreceipt_status": "1"},
    ]
    token = [
        {"hash": "0xaaa", "from": WALLET, "to": ROUTER, "contractAddress": TOKEN, "value": "1"},
        {"hash": "0xbbb", "from": WALLET, "to": ROUTER, "contractAddress": TOKEN, "value": "1"},
        {"hash": "0xccc", "from": WALLET, "to": ROUTER, "contractAddress": TOKEN, "value": "1"},
    ]
    assert patch._trace_candidate_hashes(normal, token, WALLET, {ROUTER.lower()}) == ["0xaaa"]


def test_legacy_migration_preserves_current_candidate_priority():
    candidates = ["0xTop", "0xMiddle", "0xOldest"]
    legacy = ["0xoldest", "0xmiddle", "0xtop"]
    assert patch._first_legacy_candidate(candidates, legacy) == "0xtop"


def test_legacy_migration_skips_candidates_already_migrated():
    candidates = ["0xTop", "0xNext", "0xThird"]
    legacy = ["0xnext", "0xthird"]
    assert patch._first_legacy_candidate(candidates, legacy) == "0xnext"


def _install_common(monkeypatch, chain_id, calls):
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, cid: "https://example.g.alchemy.com/v2/redacted")
    monkeypatch.setattr(patch._sibot, "platform_settings", lambda app, cid: {})
    monkeypatch.setattr(patch._sibot, "_routers", lambda app, chain: {ROUTER.lower()})

    transfer_rows = [_erc20("0xaaa", ROUTER, WALLET)]

    def asset_pages(url, wallet, address_key, categories, cutoff, max_pages, page_size, delay):
        calls.append(("asset", tuple(categories)))
        if categories == ["internal"]:
            return [], True
        return transfer_rows, True

    monkeypatch.setattr(patch._alchemy, "_asset_pages", asset_pages)
    monkeypatch.setattr(patch._alchemy, "_dedupe", lambda rows: rows)
    monkeypatch.setattr(
        patch,
        "_direct_tx_context",
        lambda url, transfers, wallet, routers: ([{
            "hash": "0xaaa",
            "from": WALLET,
            "to": ROUTER,
            "isError": "0",
            "txreceipt_status": "1",
        }], ["0xaaa"], {"0xaaa": 123}),
    )

    def normalise(rows):
        if rows and rows[0].get("category") == "erc20":
            return ([{
                "hash": "0xaaa",
                "from": WALLET,
                "to": ROUTER,
                "contractAddress": TOKEN,
                "value": "1",
            }], [])
        return ([], [])

    monkeypatch.setattr(patch._alchemy, "_normalised_transfer_rows", normalise)
    monkeypatch.setattr(
        patch._alchemy,
        "_trace_internal",
        lambda url, wallet, hashes, ts: calls.append(("trace", tuple(hashes))) or [{
            "hash": "0xaaa", "to": WALLET, "value": str(10**18), "timeStamp": "123", "isError": "0"
        }],
    )
    monkeypatch.setattr(
        patch._alchemy,
        "_store_success",
        lambda app, chain, wallet, fetched_at, normal, token, internal, complete: {
            "complete": complete,
            "internal": internal,
        },
    )
    monkeypatch.setattr(
        patch._alchemy,
        "_store_error",
        lambda app, chain, wallet, fetched_at, error: {"complete": False, "error": error},
    )
    return SimpleNamespace(chain_id=chain_id, slug="chain")


def test_arbitrum_traces_even_when_internal_transfer_api_would_be_empty(monkeypatch):
    calls = []
    chain = _install_common(monkeypatch, 42161, calls)
    result = patch.refresh_wallet_history(SimpleNamespace(), chain, WALLET)
    assert result["complete"] is True
    assert ("trace", ("0xaaa",)) in calls
    assert ("asset", ("internal",)) not in calls
    assert result["internal"]


def test_bnb_traces_even_when_internal_transfer_api_would_be_empty(monkeypatch):
    calls = []
    chain = _install_common(monkeypatch, 56, calls)
    result = patch.refresh_wallet_history(SimpleNamespace(), chain, WALLET)
    assert result["complete"] is True
    assert ("trace", ("0xaaa",)) in calls
    assert ("asset", ("internal",)) not in calls


def test_polygon_prefers_supported_internal_transfers_api(monkeypatch):
    calls = []
    chain = _install_common(monkeypatch, 137, calls)
    result = patch.refresh_wallet_history(SimpleNamespace(), chain, WALLET)
    assert result["complete"] is True
    assert ("asset", ("internal",)) in calls
    assert not any(name == "trace" for name, _ in calls)
