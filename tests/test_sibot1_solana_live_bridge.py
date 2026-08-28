from decimal import Decimal
from types import SimpleNamespace

from learnerbot import sibot1_solana_live_bridge_patch as bridge


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "csv", data_dir=tmp_path / "data")


def test_control_defaults_fail_closed(tmp_path):
    app = _app(tmp_path)
    ctl = bridge.control(app, "123")
    assert ctl["armed"] == "false"
    assert ctl["live_enabled"] == "false"
    assert ctl["auto_enabled"] == "false"
    assert bridge._entry_size(ctl) == Decimal("0.009")


def test_entry_size_is_fixed_regardless_of_requested():
    assert bridge._entry_size({"max_sol_per_trade": "0.5"}) == Decimal("0.009")
    assert bridge._entry_size({"max_sol_per_trade": "0.00001"}) == Decimal("0.009")


def test_live_revalidation_fails_closed_on_poolcheck(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "external_pool_check",
        lambda mint, cfg: {
            "decision": "SHADOW_ONLY",
            "reason_code": "LP_CONCENTRATION_RISK",
            "reason": "LP safety is not strong enough for LIVE",
        },
    )
    monkeypatch.setattr(bridge._sol, "settings", lambda app: {})
    called = {"quote": 0}

    def no_quote(*args, **kwargs):
        called["quote"] += 1
        return {}

    monkeypatch.setattr(bridge._sol, "jupiter_quote", no_quote)
    ok, reason, _ = bridge._live_entry_revalidation(object(), "Mint111111111111111111111111111111111", Decimal("0.0005"))
    assert ok is False
    assert "LP_CONCENTRATION_RISK" in reason
    assert called["quote"] == 0


def test_live_revalidation_requires_full_and_3x_reverse(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "external_pool_check",
        lambda mint, cfg: {"decision": "PASS", "reason_code": "PASS", "reason": "ok"},
    )
    monkeypatch.setattr(bridge._sol, "settings", lambda app: {"max_roundtrip_loss_pct": "3"})
    quotes = iter([
        {"outAmount": "1000000"},
        {"outAmount": "490000"},
        {"outAmount": "1450000"},
    ])
    monkeypatch.setattr(bridge._sol, "jupiter_quote", lambda *a, **k: next(quotes))
    monkeypatch.setattr(bridge, "_impact_bps", lambda quote: Decimal("25"))
    ok, reason, evidence = bridge._live_entry_revalidation(
        object(), "Mint111111111111111111111111111111111", Decimal("0.0005")
    )
    assert ok is True
    assert reason == "PASS"
    assert evidence["forward_out_raw"] == 1000000
    assert evidence["stress_out_lamports"] == 1450000


def test_exit_deferred_can_retry_after_cooldown(tmp_path, monkeypatch):
    app = _app(tmp_path)
    app.data_dir.mkdir(parents=True)
    candidate = {
        "candidate_id": "exit-1",
        "kind": "EXIT",
        "chain": "solana",
        "shadow_lot_id": "lot-1",
        "engine_id": "gpt",
        "asset": "Mint111111111111111111111111111111111",
    }
    claimed, key = bridge._claim(app, "123", candidate)
    assert claimed is True
    bridge._attempt_update(app, key, "EXIT_DEFERRED", error="temporary quote failure")
    claimed2, _ = bridge._claim(app, "123", candidate)
    assert claimed2 is False
    monkeypatch.setattr(bridge, "EXIT_RETRY_SECONDS", 0)
    claimed3, _ = bridge._claim(app, "123", candidate)
    assert claimed3 is True
