from types import SimpleNamespace

from learnerbot import solana_positive_edge_entry_gate_patch as gate


def _allow_amount_gates(monkeypatch):
    monkeypatch.setattr(gate, "_mint_loss_gate", lambda app, mint, cfg: (True, "ok", {}))
    monkeypatch.setattr(
        gate,
        "_platform_amount_gate",
        lambda app, cfg: (True, "realised profit amount exceeds loss target", {}, False),
    )


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
    _allow_amount_gates(monkeypatch)
    expected = [{"action": "BUY"}]
    monkeypatch.setattr(gate, "_PREV_PROCESS", lambda app, event: expected)
    assert gate.process_leader_event_positive_edge(app, event) is expected


def test_vmv_buy_bypasses_only_leader_median_edge_gate(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": gate.VMV_MINT}
    monkeypatch.setattr(
        gate._sol,
        "settings",
        lambda app: {"live_vmv_allowlist_enabled": "true", "live_vmv_mint": gate.VMV_MINT},
    )
    monkeypatch.setattr(
        gate,
        "_edge_ok",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("VMV must bypass only the median-return gate")),
    )
    _allow_amount_gates(monkeypatch)
    expected = [{"action": "BUY", "source": "inner-live-safety-handler"}]
    monkeypatch.setattr(gate, "_PREV_PROCESS", lambda app, event: expected)
    assert gate.process_leader_event_positive_edge(app, event) is expected


def test_vmv_cannot_bypass_actual_amount_loss_gate(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": gate.VMV_MINT}
    monkeypatch.setattr(
        gate._sol,
        "settings",
        lambda app: {"live_vmv_allowlist_enabled": "true", "live_vmv_mint": gate.VMV_MINT},
    )
    monkeypatch.setattr(gate, "_mint_loss_gate", lambda app, mint, cfg: (True, "ok", {}))
    monkeypatch.setattr(
        gate,
        "_platform_amount_gate",
        lambda app, cfg: (
            False,
            "platform realised profit amount is below loss amount",
            {"gross_profit_sol": gate.Decimal("0.003"), "gross_loss_sol": gate.Decimal("0.019"), "profit_factor": gate.Decimal("0.16")},
            False,
        ),
    )
    monkeypatch.setattr(
        gate,
        "_PREV_PROCESS",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("amount-loss gate must stop VMV BUY")),
    )
    actions = gate.process_leader_event_positive_edge(app, event)
    assert actions[0]["action"] == "REJECT"
    assert actions[0]["reason"].startswith("PLATFORM_AMOUNT_PROFIT_GATE:")


def test_vmv_exception_can_be_disabled(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": gate.VMV_MINT}
    monkeypatch.setattr(
        gate._sol,
        "settings",
        lambda app: {"live_vmv_allowlist_enabled": "false", "live_vmv_mint": gate.VMV_MINT},
    )
    monkeypatch.setattr(
        gate,
        "_edge_ok",
        lambda app, wallet, cfg: (
            False,
            "leader median return 1.000% is below LIVE edge floor 5.000%",
            {"median_return_pct": gate.Decimal("1"), "recent_median_return_pct": gate.Decimal("1")},
        ),
    )
    actions = gate.process_leader_event_positive_edge(app, event)
    assert actions[0]["action"] == "REJECT"
    assert actions[0]["reason"].startswith("POSITIVE_EDGE_GATE:")


def test_first_realised_copied_loss_quarantines_leader_for_hard_24h(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "_latest_copied_result",
        lambda app, tid, wallet: (gate.Decimal("-0.00001"), now - 7 * 3600),
    )
    monkeypatch.setattr(
        gate,
        "_PREV_COPIED_OK",
        lambda app, tid, wallet, cfg: (_ for _ in ()).throw(AssertionError("24h quarantine must reject before inner gate")),
    )
    assert not gate.copied_ok_quarantine_first_loss(
        SimpleNamespace(),
        "123",
        "leader",
        {"live_quarantine_after_first_copied_loss_minutes": "360"},
    )


def test_first_loss_quarantine_expires_after_24h(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "_latest_copied_result",
        lambda app, tid, wallet: (gate.Decimal("-0.00001"), now - 25 * 3600),
    )
    monkeypatch.setattr(gate, "_PREV_COPIED_OK", lambda app, tid, wallet, cfg: True)
    assert gate.copied_ok_quarantine_first_loss(
        SimpleNamespace(),
        "123",
        "leader",
        {"live_quarantine_after_first_copied_loss_minutes": "360"},
    )


def test_platform_amount_gate_rejects_actual_loss_pattern_during_cooldown(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "platform_live_amount_metrics",
        lambda app, cfg: {
            "closed": 20, "wins": 3, "losses": 17,
            "gross_profit_sol": gate.Decimal("0.003053606"),
            "gross_loss_sol": gate.Decimal("0.018933076"),
            "net_sol": gate.Decimal("-0.015879470"),
            "profit_factor": gate.Decimal("0.161284"),
            "latest_closed_at": now - 60,
        },
    )
    ok, reason, metrics, recovery = gate._platform_amount_gate(SimpleNamespace(), {})
    assert not ok
    assert not recovery
    assert "1.30x loss amount" in reason
    assert metrics["gross_loss_sol"] > metrics["gross_profit_sol"]


def test_platform_amount_gate_accepts_fewer_wins_when_profit_amount_is_larger(monkeypatch):
    monkeypatch.setattr(
        gate,
        "platform_live_amount_metrics",
        lambda app, cfg: {
            "closed": 5, "wins": 2, "losses": 3,
            "gross_profit_sol": gate.Decimal("0.060"),
            "gross_loss_sol": gate.Decimal("0.020"),
            "net_sol": gate.Decimal("0.040"),
            "profit_factor": gate.Decimal("3.0"),
            "latest_closed_at": 1,
        },
    )
    ok, reason, metrics, recovery = gate._platform_amount_gate(SimpleNamespace(), {})
    assert ok
    assert not recovery
    assert metrics["wins"] < metrics["losses"]
    assert metrics["gross_profit_sol"] > metrics["gross_loss_sol"] * gate.Decimal("1.30")


def test_mint_first_loss_is_quarantined_24h(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "mint_live_amount_metrics",
        lambda app, mint, cfg: {
            "closed": 1, "wins": 0, "losses": 1,
            "gross_profit_sol": gate.Decimal("0"), "gross_loss_sol": gate.Decimal("0.001"),
            "net_sol": gate.Decimal("-0.001"), "profit_factor": gate.Decimal("0"),
            "latest_closed_at": now - 60, "latest_net_sol": gate.Decimal("-0.001"),
        },
    )
    ok, reason, metrics = gate._mint_loss_gate(SimpleNamespace(), "badmint", {})
    assert not ok
    assert "quarantined" in reason
    assert metrics["losses"] == 1


def test_repeated_losing_mint_uses_72h_quarantine(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(
        gate,
        "mint_live_amount_metrics",
        lambda app, mint, cfg: {
            "closed": 3, "wins": 1, "losses": 2,
            "gross_profit_sol": gate.Decimal("0.0001"), "gross_loss_sol": gate.Decimal("0.002"),
            "net_sol": gate.Decimal("-0.0019"), "profit_factor": gate.Decimal("0.05"),
            "latest_closed_at": now - 25 * 3600, "latest_net_sol": gate.Decimal("-0.001"),
        },
    )
    ok, reason, metrics = gate._mint_loss_gate(SimpleNamespace(), "repeatbad", {})
    assert not ok
    # It would have cleared the 24h first-loss window, so rejection proves the
    # repeated-loss 72h window is active.
    assert "more minutes" in reason
    assert metrics["losses"] == 2


def test_sell_is_never_blocked_by_buy_amount_or_mint_gates(monkeypatch):
    event = {"action": "SELL", "leader_wallet": "leader", "mint": "badmint"}
    monkeypatch.setattr(
        gate,
        "_platform_amount_gate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SELL must not evaluate BUY amount gate")),
    )
    monkeypatch.setattr(
        gate,
        "_mint_loss_gate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SELL must not evaluate mint BUY gate")),
    )
    expected = [{"action": "SELL"}]
    monkeypatch.setattr(gate, "_PREV_PROCESS", lambda app, event: expected)
    assert gate.process_leader_event_positive_edge(SimpleNamespace(), event) is expected


def test_recovery_canary_is_recorded_only_after_actual_buy(monkeypatch):
    app = SimpleNamespace()
    event = {"action": "BUY", "leader_wallet": "leader", "mint": "freshmint"}
    monkeypatch.setattr(gate._sol, "settings", lambda app: {})
    monkeypatch.setattr(
        gate,
        "_edge_ok",
        lambda app, wallet, cfg: (True, "ok", {"median_return_pct": gate.Decimal("8"), "recent_median_return_pct": gate.Decimal("7")}),
    )
    monkeypatch.setattr(gate, "_mint_loss_gate", lambda app, mint, cfg: (True, "ok", {}))
    metrics = {"latest_closed_at": 123, "closed": 20}
    monkeypatch.setattr(gate, "_platform_amount_gate", lambda app, cfg: (True, "RECOVERY_CANARY", metrics, True))
    marked = []
    monkeypatch.setattr(gate, "_mark_recovery_canary", lambda app, m: marked.append(m))
    monkeypatch.setattr(gate, "_PREV_PROCESS", lambda app, event: [{"action": "BUY", "position_id": "p1"}])
    gate.process_leader_event_positive_edge(app, event)
    assert marked == [metrics]
