from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_entry_exit_liquidity_preflight_patch as patch
from learnerbot import solana_preflight_cache_patch as cache
from learnerbot import poolcheck_rug_hardening_patch as hard


def _cfg(**overrides):
    cfg = {
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
        "live_entry_require_exit_liquidity_max_bps": "500",
        "live_emergency_exit_max_combined_bps": "500",
        "live_order_slippage_bps": "50",
    }
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


def _event():
    return {
        "event_ts": int(time.time()),
        "signature": "sig-1",
        "mint": "Mint111111111111111111111111111111111111111",
        "leader_wallet": "Leader11111111111111111111111111111111111",
        "sol_amount": "1",
        "token_amount_raw": "1000000",
    }


def _quote_pair(reverse_impact=None, *, legacy=False, reverse_out=1_000_000_000):
    buy = {"outAmount": "1000000", "priceImpact": 0.1}
    reverse = {"outAmount": str(reverse_out)}
    if reverse_impact is not None:
        if legacy:
            reverse["priceImpactPct"] = reverse_impact
        else:
            reverse["priceImpact"] = reverse_impact
    return buy, reverse


def _run(monkeypatch, reverse_impact=None, *, cfg=None, legacy=False, reverse_out=1_000_000_000):
    calls = []
    quotes = list(_quote_pair(reverse_impact, legacy=legacy, reverse_out=reverse_out))

    def fake_quote(app, input_mint, output_mint, amount_raw):
        calls.append((input_mint, output_mint, int(amount_raw)))
        return quotes.pop(0)

    monkeypatch.setattr(patch._sol, "jupiter_quote", fake_quote)
    result = patch.validate_entry_with_exit_liquidity(
        SimpleNamespace(), _event(), Decimal("1"), cfg or _cfg()
    )
    return result, calls


def test_safe_reverse_exit_reuses_only_existing_two_quotes(monkeypatch):
    # 4.0% quoted impact = 400 bps; +50 bps reserved slippage = 450 bps.
    (ok, reason, detail), calls = _run(monkeypatch, 4.0)
    assert ok is True
    assert reason == "PASS_EXIT_LIQUIDITY"
    assert detail["reverse_exit_price_impact_bps"] == Decimal("400.0")
    assert detail["reverse_exit_combined_bps"] == Decimal("450.0")
    assert len(calls) == 2
    assert calls[0][2] == 1_000_000_000
    assert calls[1][2] == 1_000_000


def test_reverse_exit_above_same_five_percent_combined_ceiling_is_rejected(monkeypatch):
    # 4.6% impact + 0.5% slippage reserve = 5.1%, so no entry may proceed.
    (ok, reason, detail), calls = _run(monkeypatch, 4.6)
    assert ok is False
    assert "reverse exit liquidity rejected" in reason
    assert "510.00 bps" in reason
    assert detail["reverse_exit_liquidity_limit_bps"] == Decimal("500")
    assert len(calls) == 2


def test_missing_reverse_price_impact_fails_closed(monkeypatch):
    (ok, reason, detail), calls = _run(monkeypatch, None)
    assert ok is False
    assert "did not report price impact" in reason
    assert detail["reverse_exit_price_impact_bps"] is None
    assert len(calls) == 2


def test_config_cannot_weaken_entry_exit_liquidity_above_five_percent(monkeypatch):
    cfg = _cfg(
        live_entry_require_exit_liquidity_max_bps=900,
        live_emergency_exit_max_combined_bps=900,
    )
    (ok, reason, detail), _ = _run(monkeypatch, 4.6, cfg=cfg)
    assert ok is False
    assert detail["reverse_exit_liquidity_limit_bps"] == Decimal("500")
    assert "510.00 bps" in reason


def test_stricter_emergency_ceiling_also_tightens_entry_gate(monkeypatch):
    cfg = _cfg(live_emergency_exit_max_combined_bps=300)
    (ok, reason, detail), _ = _run(monkeypatch, 2.6, cfg=cfg)
    assert ok is False
    assert detail["reverse_exit_liquidity_limit_bps"] == Decimal("300")
    assert "310.00 bps" in reason


def test_legacy_price_impact_pct_uses_same_bps_semantics(monkeypatch):
    # Legacy 0.04 is a decimal fraction = 4% = 400 bps.
    (ok, reason, detail), _ = _run(monkeypatch, 0.04, legacy=True)
    assert ok is True
    assert reason == "PASS_EXIT_LIQUIDITY"
    assert detail["reverse_exit_price_impact_bps"] == Decimal("400.00")


def test_existing_roundtrip_gate_remains_authoritative(monkeypatch):
    # Liquidity impact itself is safe, but receiving only 95% of SOL back still
    # violates the pre-existing 3% round-trip rule. The new gate cannot weaken it.
    (ok, reason, detail), calls = _run(monkeypatch, 1.0, reverse_out=950_000_000)
    assert ok is False
    assert reason.startswith("round-trip loss")
    assert detail["roundtrip_loss_pct"] == Decimal("5.00")
    assert len(calls) == 2


def test_preflight_cache_captures_full_exit_then_stress_gate_and_keys_risk_inputs():
    # PoolCheck hardening intentionally wraps the previous full-position exit gate;
    # verify both layers remain composed rather than weakening either one.
    assert cache._PREV_VALIDATE is hard.validate_solana_entry_with_stress_exit
    assert hard._PREV_SOL_VALIDATE is patch.validate_entry_with_exit_liquidity
    assert cache._key is hard.solana_preflight_key_with_stress

    event = _event()
    a = cache._key(event, Decimal("1"), _cfg())
    b = cache._key(event, Decimal("1"), _cfg(live_entry_require_exit_liquidity_max_bps=400))
    c = cache._key(event, Decimal("1"), _cfg(live_order_slippage_bps=25))
    d = cache._key(event, Decimal("1"), _cfg(live_entry_stress_exit_multiplier=4))
    assert a != b
    assert a != c
    assert a != d
