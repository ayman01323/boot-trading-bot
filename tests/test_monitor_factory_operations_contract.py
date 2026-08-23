from pathlib import Path

from scripts import monitor_factory_operations as ops


WORKFLOW = Path(".github/workflows/monitor-factory-operations.yml")
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


def test_workflow_has_requested_monitor_factory_cadences_and_seven_agent_bus():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "cron: '*/15 * * * *'" in text
    assert "cron: '7 * * * *'" in text
    assert "cron: '17 * * * *'" in text
    assert "cron: '11 6 * * *'" in text
    assert "cron: '41 7 * * 1'" in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "AI_AGENT_BUS_URL: ws://127.0.0.1:8765" in text
    assert "{'gpt','claude','gemini','deepseek','grok','kimi','copilot'}" in text


def test_workflow_reads_production_evidence_but_has_no_repo_write_permission():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "persist-credentials: false" in text
    assert "DATA_DIR: /root/multichain-learning-bot-v2.2-fast-direct-market/data" in text
    assert "CSV_DIR: /root/multichain-learning-bot-v2.2-fast-direct-market/CSVbot" in text
    assert "git status --porcelain" in text


def test_operating_model_keeps_profit_first_and_protected_boundaries():
    text = DOC.read_text(encoding="utf-8")
    assert "money-weighted net profitability" in text
    assert "winning percentage / win rate" in text
    assert "quantity of wins versus losses" in text
    assert "gross value of wins versus gross value of losses" in text
    assert "bandwidth usage" in text.lower()
    assert "all seven agents" in text
    assert "draft PR only" in text
    assert "explicit MASTER" in text
