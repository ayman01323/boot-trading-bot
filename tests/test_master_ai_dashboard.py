from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_master_dashboard_is_loaded_and_master_only() -> None:
    loader = text("learnerbot/telegram_strategy_lab_report_patch.py")
    dashboard = text("learnerbot/telegram_master_ai_dashboard_patch.py")
    assert "telegram_master_ai_dashboard_patch" in loader
    assert "🎛 MASTER AI Dashboard" in dashboard
    assert 'callback_data": "masterai:home"' in dashboard
    assert "_ui._master(app, chat_id)" in dashboard
    assert "/masterdashboard" in dashboard
    assert "/aidashboard" in dashboard


def test_master_dashboard_lists_every_main_chain_and_uses_realised_live_results() -> None:
    dashboard = text("learnerbot/telegram_master_ai_dashboard_patch.py")
    for slug in ("solana", "bsc", "base", "ethereum", "arbitrum", "polygon"):
        assert f'"{slug}"' in dashboard
    assert "24H REALISED LIVE" in dashboard
    assert "realised_user_net_native" in dashboard
    assert "realised_net_sol" in dashboard
    assert "AUTO_OUTCOME" in dashboard
    assert "NO 24H DATA" in dashboard
    assert "PROFITABLE" in dashboard
    assert "LOSING" in dashboard


def test_master_dashboard_summarises_health_engineering_factory_and_live_waiting() -> None:
    dashboard = text("learnerbot/telegram_master_ai_dashboard_patch.py")
    assert "🤖 AI HEALTH:" in dashboard
    assert "🛠 ENGINEERING:" in dashboard
    assert "🏭 FACTORY:" in dashboard
    assert "🚀 LIVE CHANGES WAITING:" in dashboard
    assert "status='SHADOW'" in dashboard
    assert "HUMAN_APPROVAL_REQUIRED" in dashboard
    assert "recommendations" in dashboard


def test_canary_policy_requires_real_evidence_and_master_full_live_approval() -> None:
    policy = text("docs/STRATEGY_CANARY_LIVE_PROMOTION.md")
    assert "At least 24 hours" in policy
    assert "At least 10 closed real canary trades" in policy
    assert "Realised net P&L" in policy
    assert "profit factor is at least 1.10" in policy
    assert "no unresolved P0/P1 defect" in policy
    assert "MASTER dashboard must show" in policy
    assert "FULL LIVE requires explicit MASTER approval" in policy
    assert "next eligible market signal" in policy
    assert "does not immediately place a trade" in policy
