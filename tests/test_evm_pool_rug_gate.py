from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import evm_pool_rug_gate as rug


WRAPPED = "0x0000000000000000000000000000000000000001"
TOKEN = "0x0000000000000000000000000000000000000002"
TOKEN2 = "0x0000000000000000000000000000000000000003"


def _pair(chain_slug: str, *, now=1_800_000_000.0, age=7200, liquidity=100_000, volume=10_000, price="1"):
    return {
        "chainId": chain_slug,
        "dexId": "testdex",
        "pairCreatedAt": int((now - age) * 1000),
        "priceUsd": str(price),
        "liquidity": {"usd": float(liquidity)},
        "volume": {"h24": float(volume)},
    }


def _trader(chain_id=1, settings=None):
    return SimpleNamespace(
        chain=SimpleNamespace(chain_id=chain_id, slug=rug.SUPPORTED_CHAINS[chain_id]),
        wrapped=WRAPPED,
        settings=dict(settings or {}),
    )


def test_gate_covers_every_live_evm_chain():
    assert rug.SUPPORTED_CHAINS == {
        1: "ethereum",
        56: "bsc",
        137: "polygon",
        8453: "base",
        42161: "arbitrum",
    }


@pytest.mark.parametrize("chain_id", [1, 56, 137, 8453, 42161])
def test_dex_pool_screen_passes_deep_established_pool_on_every_chain(chain_id):
    now = 1_800_000_000.0
    rug._LIQ_HISTORY.clear()
    result = rug.evaluate_dexscreener(
        [_pair(rug.SUPPORTED_CHAINS[chain_id], now=now)],
        chain_id=chain_id,
        token=TOKEN,
        now_epoch=now,
    )
    assert result["decision"] == "PASS"
    assert result["reason_code"] == "DEX_POOL_PASS"


def test_pool_liquidity_collapse_below_30_percent_hard_blocks():
    now = 1_800_000_000.0
    rug._LIQ_HISTORY.clear()
    old = [_pair("base", now=now - 60, liquidity=100_000)]
    new = [_pair("base", now=now, liquidity=20_000)]
    first = rug.evaluate_dexscreener(old, chain_id=8453, token=TOKEN, now_epoch=now - 60)
    assert first["decision"] == "PASS"
    second = rug.evaluate_dexscreener(new, chain_id=8453, token=TOKEN, now_epoch=now)
    assert second["decision"] == "HARD_BLOCK"
    assert second["reason_code"] == "POOL_LIQUIDITY_COLLAPSE"
    assert second["evidence"]["dex_liquidity_retained_pct"] == Decimal("20")


def test_new_pool_and_tiny_liquidity_fail_closed():
    now = 1_800_000_000.0
    rug._LIQ_HISTORY.clear()
    tiny = rug.evaluate_dexscreener(
        [_pair("polygon", now=now, age=7200, liquidity=500)],
        chain_id=137,
        token=TOKEN,
        now_epoch=now,
    )
    assert tiny["reason_code"] == "POOL_LIQUIDITY_TOO_LOW"
    rug._LIQ_HISTORY.clear()
    fresh = rug.evaluate_dexscreener(
        [_pair("polygon", now=now, age=60, liquidity=100_000)],
        chain_id=137,
        token=TOKEN,
        now_epoch=now,
    )
    assert fresh["reason_code"] == "POOL_NEW_COOLING"


def test_goplus_honeypot_sell_restriction_and_high_tax_hard_block():
    base = {"trust_list": "0", "is_in_dex": "1", "is_open_source": "1"}
    result = rug.evaluate_goplus({**base, "is_honeypot": "1"})
    assert result["reason_code"] == "HONEYPOT"
    result = rug.evaluate_goplus({**base, "cannot_sell_all": "1"})
    assert result["reason_code"] == "TOKEN_CANNOT_SELL_ALL"
    result = rug.evaluate_goplus({**base, "sell_tax": "0.25"})
    assert result["reason_code"] == "EXCESSIVE_TOKEN_TAX"


def test_trusted_token_does_not_bypass_explicit_honeypot_or_tax_flags():
    trusted = {"trust_list": "1", "is_in_dex": "1", "is_honeypot": "1"}
    assert rug.evaluate_goplus(trusted)["reason_code"] == "HONEYPOT"
    trusted = {"trust_list": "1", "is_in_dex": "1", "sell_tax": "1"}
    assert rug.evaluate_goplus(trusted)["reason_code"] == "EXCESSIVE_TOKEN_TAX"


def test_untrusted_owner_control_risk_hard_blocks():
    result = rug.evaluate_goplus({
        "trust_list": "0",
        "is_in_dex": "1",
        "is_open_source": "1",
        "owner_change_balance": "1",
    })
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "TOKEN_CONTROL_RISK"


def test_live_external_provider_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(rug, "_fetch_goplus", lambda trader, token: (_ for _ in ()).throw(RuntimeError("offline")))
    result = rug.external_pool_rug_check(_trader(56), TOKEN)
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "GOPLUS_UNAVAILABLE"


def test_route_checks_every_non_wrapped_token(monkeypatch):
    seen = []
    def fake_check(trader, token):
        seen.append(token)
        return {"decision": "PASS", "reason_code": "PASS", "reason": "ok", "evidence": {}}
    monkeypatch.setattr(rug, "external_pool_rug_check", fake_check)
    result = rug.check_live_route(_trader(42161), [WRAPPED, TOKEN, TOKEN2, WRAPPED])
    assert result["decision"] == "PASS"
    assert seen == [TOKEN, TOKEN2]


def test_manual_buy_rug_block_happens_before_original_buy_or_signing(monkeypatch):
    called = {"original": 0}
    fake = _trader(1)
    fake._require_enabled = lambda side: None
    fake._confirm = lambda confirm: None
    fake.quote_buy = lambda token, amount: SimpleNamespace(
        token=TOKEN,
        expected_out_human="100",
        token_decimals=18,
    )
    monkeypatch.setattr(
        rug,
        "external_pool_rug_check",
        lambda trader, token: {"decision": "HARD_BLOCK", "reason_code": "HONEYPOT", "reason": "blocked", "evidence": {}},
    )
    monkeypatch.setattr(rug, "_ORIG_BUY", lambda *args, **kwargs: called.__setitem__("original", called["original"] + 1))
    with pytest.raises(rug._live.LiveTradingError, match="HONEYPOT"):
        rug.buy_with_pool_rug_gate(fake, TOKEN, "0.01", "CONFIRM")
    assert called["original"] == 0


def test_auto_cycle_rug_block_happens_before_original_prebroadcast(monkeypatch):
    called = {"original": 0}
    monkeypatch.setattr(
        rug,
        "external_pool_rug_check",
        lambda trader, token: {"decision": "HARD_BLOCK", "reason_code": "POOL_LIQUIDITY_COLLAPSE", "reason": "blocked", "evidence": {}},
    )
    monkeypatch.setattr(rug, "_ORIG_PREBROADCAST_CYCLE", lambda *args, **kwargs: called.__setitem__("original", called["original"] + 1))
    with pytest.raises(rug._live.LiveTradingError, match="POOL_LIQUIDITY_COLLAPSE"):
        rug.prebroadcast_cycle_with_pool_rug_gate(_trader(137), [WRAPPED, TOKEN, WRAPPED], "0.01", "0.001")
    assert called["original"] == 0


def test_v3_auto_cycle_uses_same_rug_gate(monkeypatch):
    seen = []
    monkeypatch.setattr(rug, "check_live_route", lambda trader, path: seen.append((trader.chain.chain_id, list(path))) or {"decision": "PASS"})
    monkeypatch.setattr(rug, "_ORIG_PREBROADCAST_V3_CYCLE", lambda *args, **kwargs: ("SIM", "BUILT"))
    result = rug.prebroadcast_v3_cycle_with_pool_rug_gate(
        _trader(8453), [WRAPPED, TOKEN, WRAPPED], [3000, 3000], "0.01", "0.001", "router", "quoter"
    )
    assert result == ("SIM", "BUILT")
    assert seen == [(8453, [WRAPPED, TOKEN, WRAPPED])]


def test_runtime_install_wraps_manual_v2_and_v3_live_paths():
    assert rug._live.LiveTrader.buy is rug.buy_with_pool_rug_gate
    assert rug._live.LiveTrader._prebroadcast_cycle is rug.prebroadcast_cycle_with_pool_rug_gate
    assert rug._live.LiveTrader._prebroadcast_v3_cycle is rug.prebroadcast_v3_cycle_with_pool_rug_gate
    assert getattr(rug._live.LiveTrader, "_evm_pool_rug_gate_installed", False) is True
