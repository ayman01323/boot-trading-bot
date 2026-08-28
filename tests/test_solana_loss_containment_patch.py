from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_loss_containment_patch as patch


def test_settings_keep_hard_stop_but_do_not_clip_bvkg_style_winner(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_PREV_SETTINGS",
        lambda app: {
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "break_even_trigger_pct": "3",
            "trailing_trigger_pct": "5",
            "trailing_gap_pct": "2",
        },
    )
    cfg = patch.settings_loss_containment(SimpleNamespace())
    assert Decimal(cfg["stop_loss_pct"]) == Decimal("5")
    assert Decimal(cfg["take_profit_pct"]) >= Decimal("100")
    assert Decimal(cfg["break_even_trigger_pct"]) >= Decimal("10")
    assert Decimal(cfg["trailing_trigger_pct"]) >= Decimal("20")
    assert Decimal(cfg["trailing_gap_pct"]) >= Decimal("10")


def test_positive_bvkg_style_position_does_not_enter_hard_exit(monkeypatch):
    position = {
        "position_id": "bvkg",
        "telegram_id": "1",
        "status": "OPEN",
        "mode": "LIVE",
        "unrealised_pct": 35.56,
    }
    monkeypatch.setattr(patch, "_open_live_positions", lambda app: [position])
    monkeypatch.setattr(patch, "_load_state", lambda app, pid: {})
    monkeypatch.setattr(patch._sol, "settings", lambda app: {"stop_loss_pct": "5"})
    calls = []
    monkeypatch.setattr(patch, "_attempt_required_exit", lambda app, pos, state: calls.append(pos["position_id"]))
    patch._enforce_sticky_exits(SimpleNamespace())
    assert calls == []


def test_loss_at_stop_becomes_sticky_and_attempts_full_exit(monkeypatch):
    position = {
        "position_id": "e30",
        "telegram_id": "1",
        "status": "OPEN",
        "mode": "LIVE",
        "unrealised_pct": -5.01,
    }
    monkeypatch.setattr(patch, "_open_live_positions", lambda app: [position])
    monkeypatch.setattr(patch, "_load_state", lambda app, pid: {})
    monkeypatch.setattr(patch._sol, "settings", lambda app: {"stop_loss_pct": "5"})
    marked = []
    attempted = []

    def mark(app, pos, pct, source):
        marked.append((pos["position_id"], Decimal(pct), source))
        return {"required": True, "attempts": 0}

    monkeypatch.setattr(patch, "_mark_required", mark)
    monkeypatch.setattr(patch, "_attempt_required_exit", lambda app, pos, state: attempted.append(pos["position_id"]))
    patch._enforce_sticky_exits(SimpleNamespace())
    assert marked and marked[0][0] == "e30"
    assert marked[0][1] <= Decimal("-5")
    assert attempted == ["e30"]


def test_sticky_exit_does_not_cancel_after_price_rebound(monkeypatch):
    position = {
        "position_id": "e30",
        "telegram_id": "1",
        "status": "OPEN",
        "mode": "LIVE",
        "unrealised_pct": 2.0,
    }
    monkeypatch.setattr(patch, "_open_live_positions", lambda app: [position])
    monkeypatch.setattr(patch, "_load_state", lambda app, pid: {"required": True, "attempts": 4})
    monkeypatch.setattr(patch._sol, "settings", lambda app: {"stop_loss_pct": "5"})
    attempted = []
    monkeypatch.setattr(patch, "_attempt_required_exit", lambda app, pos, state: attempted.append((pos["position_id"], state["attempts"])))
    patch._enforce_sticky_exits(SimpleNamespace())
    assert attempted == [("e30", 4)]


def test_hard_exit_reason_uses_existing_emergency_guard_not_bypass():
    assert patch._HARD_EXIT_REASON in patch._emergency._LOSS_EXIT_REASONS
