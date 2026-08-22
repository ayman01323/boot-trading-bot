from types import SimpleNamespace

from learnerbot import sibot_alchemy_internal_trace_patch as patch


WALLET = "0x0000000000000000000000000000000000000001"
ROUTER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000010"


def test_trace_candidates_are_only_successful_direct_router_token_flows():
    normal = [
        {"hash": "0xaaa", "from": WALLET, "to": ROUTER, "isError": "0", "txreceipt_status": "1"},
        {"hash": "0xbbb", "from": WALLET, "to": ROUTER, "isError": "1", "txreceipt_status": "0"},
        {"hash": "0xccc", "from": WALLET, "to": "0x0000000000000000000000000000000000000003", "isError": "0", "txreceipt_status": "1"},
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
    # Database age/order is deliberately reversed. Current candidate quality/order
    # must win during the one-time Etherscan -> Alchemy migration.
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

    transfer_rows = [{"category": "erc20", "hash": "0xaaa"}]

    def asset_pages(url, wallet, address_key, categories, cutoff, max_pages, page_size, delay):
        calls.append(("asset", tuple(categories)))
        if categories == ["internal"]:
            return [], True
        return transfer_rows, True

    monkeypatch.setattr(patch._alchemy, "_asset_pages", asset_pages)
    monkeypatch.setattr(patch._alchemy, "_dedupe", lambda rows: rows)
    monkeypatch.setattr(
        patch._alchemy,
        "_tx_context",
        lambda url, transfers, wallet: ([{
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
