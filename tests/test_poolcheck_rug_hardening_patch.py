from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from learnerbot import evm_pool_rug_gate as evm
from learnerbot import poolcheck_rug_hardening_patch as hard
from learnerbot import solana_entry_exit_liquidity_preflight_patch as sol_exit
from learnerbot import solana_preflight_cache_patch as sol_cache


TOKEN = "0x0000000000000000000000000000000000000002"
WRAPPED = "0x0000000000000000000000000000000000000001"


def _safe_goplus(**overrides):
    base = {
        "trust_list": "0",
        "is_in_dex": "1",
        "is_open_source": "1",
        "holder_count": "200",
        "lp_holder_count": "12",
    }
    base.update(overrides)
    return base


def _pair(*, liquidity=65_000, age=7200, now=1_800_000_000.0):
    return {
        "chainId": "base",
        "dexId": "testdex",
        "pairCreatedAt": int((now - age) * 1000),
        "priceUsd": "1",
        "liquidity": {"usd": liquidity},
        "volume": {"h24": 12_000},
        "txns": {
            "m5": {"buys": 5, "sells": 2},
            "h1": {"buys": 50, "sells": 30},
            "h6": {"buys": 120, "sells": 90},
            "h24": {"buys": 200, "sells": 160},
        },
    }


def test_65k_deep_pool_is_not_a_safety_whitelist():
    now = 1_800_000_000.0
    evm._LIQ_HISTORY.clear()
    pool = evm.evaluate_dexscreener(
        [_pair(now=now)],
        chain_id=8453,
        token=TOKEN,
        now_epoch=now,
    )
    assert pool["decision"] == "PASS"
    assert pool["evidence"]["dex_liquidity_usd_total"] == Decimal("65000")
    assert pool["evidence"]["liquidity_amount_is_not_safety_signal"] is True
    assert pool["evidence"]["dex_txns_h1_total"] == 80

    token = evm.evaluate_goplus(
        _safe_goplus(
            holders=[
                {"address": "0x0000000000000000000000000000000000000011", "percent": "0.30"},
                {"address": "0x0000000000000000000000000000000000000012", "percent": "0.22"},
                {"address": "0x0000000000000000000000000000000000000013", "percent": "0.12"},
            ]
        )
    )
    assert token["decision"] == "HARD_BLOCK"
    assert token["reason_code"] == "HOLDER_CONCENTRATION_RISK"
    assert token["evidence"]["top10_holder_pct"] == Decimal("64.00")


def test_creator_owner_concentration_blocks_regardless_of_liquidity():
    result = evm.evaluate_goplus(
        _safe_goplus(
            creator_address="0x0000000000000000000000000000000000000011",
            creator_percent="0.12",
        )
    )
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "CREATOR_OWNER_CONCENTRATION"
    assert result["evidence"]["creator_owner_control_pct"] == Decimal("12.00")


class _QuoteCall:
    def __init__(self, output):
        self.output = int(output)

    def call(self):
        return [1, self.output]


class _RouterFunctions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def getAmountsOut(self, amount, path):
        self.inputs.append(int(amount))
        return _QuoteCall(self.outputs.pop(0))


def _evm_trader(outputs, **settings):
    functions = _RouterFunctions(outputs)
    trader = SimpleNamespace(
        wrapped=WRAPPED,
        router=SimpleNamespace(functions=functions),
        settings=settings,
    )
    return trader, functions


def test_evm_full_position_can_pass_but_3x_stress_exit_can_block():
    # 1x returns 0.99 ETH from a 1 ETH reference (100 bps loss).
    # 3x returns only 2 ETH from a 3 ETH reference (~3333 bps loss).
    trader, functions = _evm_trader([990_000_000_000_000_000, 2_000_000_000_000_000_000])
    result = evm._manual_roundtrip_check(trader, TOKEN, Decimal("1"), 1_000_000)
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "STRESS_EXIT_DEPTH"
    assert functions.inputs == [1_000_000, 3_000_000]
    assert result["evidence"]["stress_exit_multiplier"] == Decimal("3")


def test_evm_stress_multiplier_cannot_be_weakened_below_3x():
    trader, functions = _evm_trader(
        [990_000_000_000_000_000, 2_970_000_000_000_000_000],
        live_entry_stress_exit_multiplier="1",
    )
    result = evm._manual_roundtrip_check(trader, TOKEN, Decimal("1"), 1_000_000)
    assert result["decision"] == "PASS"
    assert functions.inputs[-1] == 3_000_000
    assert result["evidence"]["stress_exit_multiplier"] == Decimal("3")


def _sol_cfg(**overrides):
    cfg = {
        "max_roundtrip_loss_pct": "3",
        "live_order_slippage_bps": "50",
        "live_entry_require_exit_liquidity_max_bps": "500",
        "live_emergency_exit_max_combined_bps": "500",
        "live_entry_stress_exit_multiplier": "3",
    }
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


def _sol_event():
    return {
        "mint": "Mint111111111111111111111111111111111111111",
        "event_ts": 1_800_000_000,
    }


def test_solana_1x_pass_but_3x_stress_impact_blocks(monkeypatch):
    monkeypatch.setattr(
        hard,
        "_PREV_SOL_VALIDATE",
        lambda app, event, allocation, cfg: (
            True,
            "PASS_EXIT_LIQUIDITY",
            {
                "out_raw": 1_000_000,
                "reverse_exit_liquidity_limit_bps": Decimal("500"),
                "reverse_exit_reserved_slippage_bps": Decimal("50"),
            },
        ),
    )
    calls = []

    def quote(app, input_mint, output_mint, amount):
        calls.append(int(amount))
        return {"outAmount": "3000000000", "priceImpact": 4.6}

    monkeypatch.setattr(sol_exit._sol, "jupiter_quote", quote)
    ok, reason, detail = hard.validate_solana_entry_with_stress_exit(
        SimpleNamespace(), _sol_event(), Decimal("1"), _sol_cfg()
    )
    assert ok is False
    assert "stress reverse exit liquidity rejected" in reason
    assert detail["stress_reverse_combined_bps"] == Decimal("510.0")
    assert calls == [3_000_000]


def test_solana_stress_multiplier_cannot_be_weakened_below_3x(monkeypatch):
    monkeypatch.setattr(
        hard,
        "_PREV_SOL_VALIDATE",
        lambda app, event, allocation, cfg: (
            True,
            "PASS_EXIT_LIQUIDITY",
            {
                "out_raw": 1_000_000,
                "reverse_exit_liquidity_limit_bps": Decimal("500"),
                "reverse_exit_reserved_slippage_bps": Decimal("50"),
            },
        ),
    )
    calls = []

    def quote(app, input_mint, output_mint, amount):
        calls.append(int(amount))
        return {"outAmount": "2970000000", "priceImpact": 1.0}

    monkeypatch.setattr(sol_exit._sol, "jupiter_quote", quote)
    ok, reason, detail = hard.validate_solana_entry_with_stress_exit(
        SimpleNamespace(),
        _sol_event(),
        Decimal("1"),
        _sol_cfg(live_entry_stress_exit_multiplier=1),
    )
    assert ok is True
    assert reason == "PASS_EXIT_LIQUIDITY"
    assert calls == [3_000_000]
    assert detail["stress_exit_multiplier"] == Decimal("3")
    assert detail["stress_roundtrip_loss_pct"] == Decimal("1.00")


def test_preflight_cache_key_includes_stress_multiplier():
    event = _sol_event()
    a = sol_cache._key(event, Decimal("1"), _sol_cfg(live_entry_stress_exit_multiplier=3))
    b = sol_cache._key(event, Decimal("1"), _sol_cfg(live_entry_stress_exit_multiplier=4))
    assert a != b
