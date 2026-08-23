from pathlib import Path

from learnerbot import monitor_factory_pipeline as mf


class App:
    def __init__(self, root: Path):
        self.data_dir = root / "data"
        self.csv_dir = root / "CSVbot"


def test_finding_dedupes_and_package_has_no_live_authority(tmp_path):
    app = App(tmp_path)
    first = mf.record_finding(
        app,
        lane="strategy",
        finding_type="problem",
        classification="strategy",
        severity="P2",
        title="Test strategy deteriorated",
        scope="SOLANA",
        strategy_id="s1",
        source_version="v1",
        evidence={"net_profit": "-5", "profit_factor": "0.7"},
        recommendation="Rework in SHADOW",
        acceptance_test="Positive net result after costs",
        now=100,
    )
    second = mf.record_finding(
        app,
        lane="STRATEGY",
        finding_type="PROBLEM",
        classification="STRATEGY",
        severity="P1",
        title="Test strategy deteriorated",
        scope="SOLANA",
        strategy_id="s1",
        source_version="v1",
        evidence={"net_profit": "-7", "profit_factor": "0.5"},
        recommendation="Rework in SHADOW",
        acceptance_test="Positive net result after costs",
        now=200,
    )
    assert first["finding_id"] == second["finding_id"]
    assert second["occurrences"] == 2

    package = mf.package_for_finding(app, second, now=200)
    authority = package["payload"]["factory_authority"]
    assert authority["research"] is True
    assert authority["propose_shadow_change"] is True
    assert authority["draft_pr"] is True
    assert authority["trade"] is False
    assert authority["arm_live"] is False
    assert authority["change_capital"] is False
    assert authority["change_wallet_or_signing"] is False
    assert authority["bypass_safety_gate"] is False
    assert authority["merge_or_deploy_without_existing_authorisation"] is False
    assert "MASTER_CANARY_APPROVAL" in package["payload"]["promotion_path"]
    assert "MASTER_FULL_LIVE_APPROVAL" in package["payload"]["promotion_path"]


def test_money_weighted_economics_outrank_high_win_rate():
    portfolio = {
        "strategies": [
            {
                "metrics": {
                    "windows": 3,
                    "opportunities": 20,
                    "eligible_opportunities": 15,
                    "trades": 10,
                    "wins": 9,
                    "losses": 1,
                    "gross_profit": "9",
                    "gross_loss": "20",
                    "fees": "1",
                    "slippage_cost": "1",
                    "net_profit": "-13",
                    "execution_failures": 0,
                }
            }
        ]
    }
    kpi = mf._portfolio_kpis(portfolio)
    assert kpi["win_rate_pct"] == 90.0
    assert kpi["wins_exceed_losses_count"] is True
    assert kpi["gross_profit_exceeds_gross_loss"] is False
    assert kpi["primary_target_pass"] is False
    assert kpi["secondary_three_way_target_pass"] is False


def test_pending_packages_are_severity_ordered(tmp_path):
    app = App(tmp_path)
    for severity, title in (("P3", "minor"), ("P1", "major"), ("P2", "middle")):
        finding = mf.record_finding(
            app,
            lane="ENGINEERING",
            finding_type="PROBLEM",
            classification="INFRASTRUCTURE",
            severity=severity,
            title=title,
            evidence={"x": severity},
            now=100,
        )
        mf.queue_finding(app, finding, now=100)
    pending = mf.pending_packages(app, limit=10)
    assert [p["severity"] for p in pending] == ["P1", "P2", "P3"]


def test_reviewed_package_cannot_be_returned_as_pending_without_requeue(tmp_path):
    app = App(tmp_path)
    finding = mf.record_finding(
        app,
        lane="STRATEGY",
        finding_type="OPPORTUNITY",
        classification="RESEARCH",
        severity="P3",
        title="Research opportunity",
        evidence={"source": "test"},
        now=100,
    )
    package = mf.queue_finding(app, finding, now=100)
    mf.set_package_state(app, package["package_id"], "REVIEWED", review={"disposition": "KEEP_MONITORING"}, now=110)
    assert mf.pending_packages(app, limit=10) == []
    summary = mf.status_summary(app)
    assert summary["packages"]["REVIEWED"] == 1
    assert summary["live_authority"] is False
