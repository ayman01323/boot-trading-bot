import time

from sibot1_engines._shared.solana_dev_flow import SolanaDeveloperFlowResolver


def _resolver_with_rpc(tx):
    resolver = SolanaDeveloperFlowResolver(cache_seconds=30, unknown_cache_seconds=5, max_signatures=3)
    resolver._rugcheck_report = lambda mint: {
        "creator": "DEV",
        "mintAuthority": None,
        "freezeAuthority": None,
        "lpLockedPct": 90,
    }

    def rpc(method, params):
        if method == "getTokenAccountsByOwner":
            return {"value": [{"pubkey": "ATA"}]}
        if method == "getSignaturesForAddress":
            return [{"signature": "sig1", "blockTime": int(time.time()) - 5, "err": None}]
        if method == "getTransaction":
            return tx
        raise AssertionError(method)

    resolver._rpc = rpc
    return resolver


def _tx(target_before, target_after, *, native_before=1000, native_after=1000):
    return {
        "transaction": {"message": {"accountKeys": [{"pubkey": "DEV", "signer": True}]}},
        "meta": {
            "err": None,
            "preBalances": [native_before],
            "postBalances": [native_after],
            "preTokenBalances": [
                {"owner": "DEV", "mint": "MINT", "uiTokenAmount": {"amount": str(target_before)}}
            ],
            "postTokenBalances": [
                {"owner": "DEV", "mint": "MINT", "uiTokenAmount": {"amount": str(target_after)}}
            ],
        },
    }


def test_no_developer_sale_with_complete_history_is_known_safe():
    resolver = _resolver_with_rpc(_tx(100, 100))
    result = resolver.resolve("MINT")
    assert result.known is True
    assert result.selling is False
    assert result.coverage_complete is True
    assert result.mint_authority_present is False
    assert result.freeze_authority_present is False
    assert result.lp_locked_pct == "90"


def test_mint_decrease_with_native_proceeds_is_classified_as_sale():
    resolver = _resolver_with_rpc(_tx(100, 50, native_before=1000, native_after=2000))
    result = resolver.resolve("MINT")
    assert result.known is True
    assert result.selling is True
    assert "counter-asset proceeds" in result.reason


def test_plain_outbound_transfer_is_unknown_not_false_safe():
    resolver = _resolver_with_rpc(_tx(100, 50, native_before=1000, native_after=900))
    result = resolver.resolve("MINT")
    assert result.known is False
    assert result.selling is False
    assert result.outbound_unclassified is True
    assert result.reason == "developer_outbound_transfer_unclassified"


def test_unverified_creator_without_token_account_stays_unknown():
    resolver = SolanaDeveloperFlowResolver(cache_seconds=30, unknown_cache_seconds=5)
    resolver._rugcheck_report = lambda mint: {"creator": "DEV"}
    resolver._rpc = lambda method, params: {"value": []}
    result = resolver.resolve("MINT")
    assert result.known is False
    assert result.reason == "creator_token_account_missing"
