from types import SimpleNamespace

from learnerbot import solana_positive_edge_entry_gate_patch as gate


def test_median_even_and_odd():
    assert gate._median([gate.Decimal("1"), gate.Decimal("3"), gate.Decimal("2")]) == gate.Decimal("2")
    assert gate._median([gate.Decimal("1"), gate.Decimal("4"), gate.Decimal("2"), gate.Decimal("3")]) == gate.Decimal("2.5")


def test_edge_gate_rejects_small_typical_returns(monkeypatch):
    monkeypatch.setattr(
        gate,
        "leader_return_edge",
        lambda app, wallet, cfg: {
            "closed": 10,
            "median_return_pct": gate.Decimal("2.5"),
            "recent_closed": 10,
            "recent_median_return_pct": gate.Decimal("2.0"),
            "median_positive_return_pct": gate.Decimal("8"),
        },
    )
    ok, reason, metrics = gate._edge_ok(
        SimpleNamespace(),
        "leader",
        {
            "min_closed_trades": "5",
            "live_min_leader_median_return_pct": "5",
            "live_min_leader_recent_median_return_pct": "4",
        },
    )
    assert not ok
    assert "below LIVE edge floor" in reason
    assert metrics["median_return_pct"] == gate.Decimal("2.5")


def test_buy_rejected_before_previous_handler_when_edge_is_insufficient(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": "mint"}
    monkeypatch.setattr(gate._sol, "settings", lambda app: {})
    monkeypatch.setattr(
        gate,
        "_edge_ok",
        lambda app, wallet, cfg: (
            False,
            "leader median return 1.000% is below LIVE edge floor 5.000%",
            {"median_return_pct": gate.Decimal("1"), "recent_median_return_pct": gate.Decimal("1")},
        ),
    )
    monkeypatch.setattr(
        gate,
        "_PREV_PROCESS",
        lambda app, event: (_ for _ in ()).throw(AssertionError("rejected BUY must not reach execution handler")),
    )
    actions = gate.process_leader_event_positive_edge(app, event)
    assert actions[0]["action"] == "REJECT"
    assert actions[0]["reason"].startswith("POSITIVE_EDGE_GATE:")


def test_buy_with_large_edge_delegates_to_existing_live_handler(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": "mint"}
    monkeypatch.setattr(gate._sol, "settings", lambda app: {})
    monkeypatch.setattr(
        gate,
        "_edge_ok",
        lambda app, wallet, cfg: (
            True,
            "ok",
            {"median_return_pct": gate.Decimal("7"), "recent_median_return_pct": gate.Decimal("6")},
        ),
    )
    expected = [{"action": "BUY"}]
    monkeypatch.setattr(gate, "_PREV_PROCESS", lambda app, event: expected)
    assert gate.process_leader_event_positive_edge(app, event) is expected


def test_first_realised_copied_loss_quarantines_leader(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "_latest_copied_result",
        lambda app, tid, wallet: (gate.Decimal("-0.00001"), now - 60),
    )
    monkeypatch.setattr(
        gate,
        "_PREV_COPIED_OK",
        lambda app, tid, wallet, cfg: (_ for _ in ()).throw(AssertionError("quarantine must reject before inner gate")),
    )
    assert not gate.copied_ok_quarantine_first_loss(
        SimpleNamespace(),
        "123",
        "leader",
        {"live_quarantine_after_first_copied_loss_minutes": "360"},
    )


def test_first_loss_quarantine_expires(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "_latest_copied_result",
        lambda app, tid, wallet: (gate.Decimal("-0.00001"), now - 7 * 3600),
    )
    monkeypatch.setattr(gate, "_PREV_COPIED_OK", lambda app, tid, wallet, cfg: True)
    assert gate.copied_ok_quarantine_first_loss(
        SimpleNamespace(),
        "123",
        "leader",
        {"live_quarantine_after_first_copied_loss_minutes": "360"},
    )
