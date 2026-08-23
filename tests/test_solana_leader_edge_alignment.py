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
