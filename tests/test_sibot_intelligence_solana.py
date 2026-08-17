from decimal import Decimal

from learnerbot import sibot_intelligence_patch as intel
from learnerbot import solana_sibot as sol


def test_adaptive_exit_break_even_trailing_and_leader_loss_cap():
    cfg = {
        "adaptive_exit_enabled": "true",
        "break_even_trigger_pct": "5",
        "break_even_floor_pct": "0.10",
        "trailing_trigger_pct": "10",
        "trailing_gap_pct": "5",
        "leader_exit_loss_cap_pct": "2.5",
    }
    assert intel._adaptive_exit_reason(cfg, {"peak_unrealised_pct": 6, "leader_exit_pending": 0}, {"net_pct": Decimal("0.05")}) == "BREAK_EVEN_PROTECT"
    assert intel._adaptive_exit_reason(cfg, {"peak_unrealised_pct": 18, "leader_exit_pending": 0}, {"net_pct": Decimal("12")}) == "TRAILING_PROFIT_PROTECT"
    assert intel._adaptive_exit_reason(cfg, {"peak_unrealised_pct": 1, "leader_exit_pending": 1}, {"net_pct": Decimal("-3")}) == "LEADER_EXIT_LOSS_CAP"
    assert intel._adaptive_exit_reason(cfg, {"peak_unrealised_pct": 4, "leader_exit_pending": 0}, {"net_pct": Decimal("2")}) is None


def _tx(wallet, pre_sol, post_sol, pre_token, post_token, signature="sig"):
    return {
        "slot": 123,
        "blockTime": 1_800_000_000,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [{"pubkey": wallet, "signer": True, "writable": True}],
            },
        },
        "meta": {
            "err": None,
            "logMessages": ["Program log: Instruction: Swap"],
            "preBalances": [pre_sol],
            "postBalances": [post_sol],
            "preTokenBalances": [{
                "owner": wallet,
                "mint": "TokenMint1111111111111111111111111111111111",
                "uiTokenAmount": {"amount": str(pre_token), "decimals": 6},
            }],
            "postTokenBalances": [{
                "owner": wallet,
                "mint": "TokenMint1111111111111111111111111111111111",
                "uiTokenAmount": {"amount": str(post_token), "decimals": 6},
            }],
        },
    }


def test_classify_solana_buy_and_sell_from_finalized_balance_deltas():
    wallet = "Trader11111111111111111111111111111111111111"
    buy = sol.classify_swap(_tx(wallet, 10_000_000_000, 9_000_000_000, 0, 2_000_000, "buySig"), wallet)
    assert buy["action"] == "BUY"
    assert buy["token_amount_raw"] == 2_000_000
    assert buy["sol_amount"] == Decimal("1")

    sell = sol.classify_swap(_tx(wallet, 9_000_000_000, 9_500_000_000, 2_000_000, 1_000_000, "sellSig"), wallet)
    assert sell["action"] == "SELL"
    assert sell["token_amount_raw"] == 1_000_000
    assert sell["sol_amount"] == Decimal("0.5")
    assert sell["sell_pct"] == 50.0


def test_solana_fifo_matches_realised_round_trip_profit():
    events = [
        {"action": "BUY", "wallet": "w", "mint": "m", "decimals": 6, "token_amount_raw": 100, "sol_amount": Decimal("1"), "signature": "b", "event_ts": 100},
        {"action": "SELL", "wallet": "w", "mint": "m", "decimals": 6, "token_amount_raw": 100, "sol_amount": Decimal("1.2"), "signature": "s", "event_ts": 200},
    ]
    rows = sol._match_events("w", events)
    assert len(rows) == 1
    assert Decimal(rows[0]["net_sol"]) == Decimal("0.2")
    assert rows[0]["hold_seconds"] == 100
