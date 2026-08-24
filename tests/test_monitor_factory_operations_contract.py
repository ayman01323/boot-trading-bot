from pathlib import Path

from scripts import monitor_factory_operations as ops


CENTRAL = Path("scripts/central_report_scheduler.py")
CONTROL = Path("learnerbot/report_schedule_control.py")
RUNTIME = Path("learnerbot/telegram_report_schedule_patch.py")
LEGACY_WORKFLOW = Path(".github/workflows/monitor-factory-operations.yml")
DOC = Path("docs/MONITOR_FACTORY_OPERATING_MODEL.md")


def test_factory_panel_scales_with_severity_and_keeps_gpt():
    p0 = ops._panel_for({"package_id": "pkg-a", "severity": "P0"})
    p2 = ops._panel_for({"package_id": "pkg-b", "severity": "P2"})
    p3 = ops._panel_for({"package_id": "pkg-c", "severity": "P3"})
    assert len(p0) == 7 and set(p0) == set(ops.AGENT_ORDER)
    assert len(p2) == 4
    assert len(p3) == 3
    assert p0[0] == p2[0] == p3[0] == "gpt"
    assert len(set(p2)) == len(p2)
    assert len(set(p3)) == len(p3)


def test_monitor_factory_cadences_are_centralised_in_runtime():
    assert not LEGACY_WORKFLOW.exists()
    control = CONTROL.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    central = CENTRAL.read_text(encoding="utf-8")
    assert "MIN_INTERVAL_HOURS = 4" in control
    assert '"trade": {' in control and '"default_hours": 4' in control
    assert '"engineering": {' in control and '"default_hours": 48' in control
    assert '"strategy": {' in control and '"default_hours": 48' in control
    assert '"factory": {' in control and '"default_hours": 6' in control
    assert '"engineering_ai": {' in control
    assert '"seven_agent": {' in control and '"default_hours": 168' in control
    assert "time.sleep(300)" in runtime
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert "ops._panel_for = lambda package: list(AGENTS)" in central


def test_central_monitor_factory_has_no_repository_or_live_execution_authority():
    central = CENTRAL.read_text(encoding="utf-8")
    assert "subprocess.run" not in central
    assert "gh workflow run" not in central
    assert "git push" not in central
    assert "merge_pull_request" not in central
    assert "LIVE" not in central or "Do not edit, deploy, trade, alter LIVE" in central
    assert '"authority": "MONITOR_AND_ESCALATE_ONLY"' in central
    assert '"changes_trading_state": False' in central


def test_operating_model_keeps_profit_first_and_protected_boundaries():
    text = DOC.read_text(encoding="utf-8")
    assert "money-weighted net profitability" in text
    assert "winning percentage / win rate" in text
    assert "quantity of wins versus losses" in text
    assert "gross value of wins versus gross value of losses" in text
    assert "bandwidth usage" in text.lower()
    assert "all seven agents" in text
    assert "draft PR only" in text
    assert "MASTER CANARY APPROVAL" in text
    assert "MASTER FULL-LIVE APPROVAL" in text
