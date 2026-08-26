from decimal import Decimal

from learnerbot import solana_leader_edge_alignment_patch as patch


def _base_metrics():
    return {
        "median_return_pct": Decimal("6.0"),
        "recent_median_return_pct": Decimal("5.0"),
    }


def test_selector_rejects_historical_median_below_live_floor(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_HISTORICAL_OK", lambda metrics, cfg: True)
    metrics = _base_metrics()
    metrics["median_return_pct"] = Decimal("0.936")
    cfg = {
        "live_min_leader_median_return_pct": "5.0",
        "live_min_leader_recent_median_return_pct": "4.0",
    }
    assert patch.historical_ok(metrics, cfg) is False


def test_selector_rejects_recent_median_below_live_floor(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_HISTORICAL_OK", lambda metrics, cfg: True)
    metrics = _base_metrics()
    metrics["recent_median_return_pct"] = Decimal("3.9")
    cfg = {
        "live_min_leader_median_return_pct": "5.0",
        "live_min_leader_recent_median_return_pct": "4.0",
    }
    assert patch.historical_ok(metrics, cfg) is False


def test_selector_accepts_candidate_that_meets_same_live_floors(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_HISTORICAL_OK", lambda metrics, cfg: True)
    cfg = {
        "live_min_leader_median_return_pct": "5.0",
        "live_min_leader_recent_median_return_pct": "4.0",
    }
    assert patch.historical_ok(_base_metrics(), cfg) is True


def test_existing_quality_failure_still_rejects(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_HISTORICAL_OK", lambda metrics, cfg: False)
    assert patch.historical_ok(_base_metrics(), {}) is False


def test_quality_metrics_carries_live_edge_evidence(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_QUALITY_METRICS", lambda app, wallet, cfg: {"profit_factor": Decimal("2")})
    monkeypatch.setattr(
        patch._edge,
        "leader_return_edge",
        lambda app, wallet, cfg: {
            "closed": 20,
            "median_return_pct": Decimal("5.5"),
            "recent_closed": 10,
            "recent_median_return_pct": Decimal("4.4"),
        },
    )
    out = patch.quality_metrics(object(), "wallet", {})
    assert out["edge_closed"] == 20
    assert out["median_return_pct"] == Decimal("5.5")
    assert out["recent_edge_closed"] == 10
    assert out["recent_median_return_pct"] == Decimal("4.4")


def test_selection_lookback_uses_longer_evidence_without_lowering_strategy_window():
    assert patch._selection_lookback_days({"lookback_days": "60"}) == 180
    assert patch._selection_lookback_days({"lookback_days": "60", "leader_selection_lookback_days": "300"}) == 300
    assert patch._selection_lookback_days({"lookback_days": "250", "leader_selection_lookback_days": "180"}) == 250
    assert patch._selection_lookback_days({"lookback_days": "60", "leader_selection_lookback_days": "999"}) == 365


def test_broader_selector_evaluates_candidates_beyond_display_top20(monkeypatch):
    candidates = [{"wallet": f"wallet-{i}"} for i in range(25)]
    seen = []

    def fake_metrics(app, wallet, cfg):
        seen.append(wallet)
        return {
            "wallet": wallet,
            "profit_factor": Decimal("2"),
            "net": Decimal("1"),
            "win_rate": Decimal("70"),
            "drawdown_pct": Decimal("5"),
            "last_activity_ts": 1,
            "median_return_pct": Decimal("6"),
            "recent_median_return_pct": Decimal("5"),
        }

    monkeypatch.setattr(patch, "quality_metrics", fake_metrics)
    monkeypatch.setattr(patch, "historical_ok", lambda metrics, cfg: metrics["wallet"] == "wallet-24")

    qualified = patch._qualified_candidates(object(), {}, candidates)

    assert seen == [f"wallet-{i}" for i in range(25)]
    assert [item[0]["wallet"] for item in qualified] == ["wallet-24"]


def test_runtime_refresh_hook_is_broader_selector():
    assert patch._sol.refresh_rankings is patch.refresh_rankings
