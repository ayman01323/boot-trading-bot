from pathlib import Path


LEGACY_WORKFLOW = Path('.github/workflows/gpt-master-cycle-dispatcher.yml')
CENTRAL = Path('scripts/central_report_scheduler.py')
CONTROL = Path('learnerbot/report_schedule_control.py')
RUNTIME = Path('learnerbot/telegram_report_schedule_patch.py')


def _text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def test_legacy_github_dispatcher_stays_retired_after_runtime_centralisation():
    # The five-minute GitHub dispatcher was intentionally removed when report and
    # Factory scheduling moved into the VPS runtime. Recreating it would duplicate
    # paid model calls and reintroduce competing schedulers.
    assert not LEGACY_WORKFLOW.exists()


def test_central_factory_invites_all_seven_and_keeps_gpt_master():
    text = _text(CENTRAL)
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in text
    assert 'ops._panel_for = lambda package: list(AGENTS)' in text
    assert 'out["master"] = "gpt"' in text
    assert '"STRATEGY_FACTORY_REVIEW"' in text
    assert '"factory-review"' in text


def test_runtime_schedule_replaces_five_minute_github_polling():
    control = _text(CONTROL)
    runtime = _text(RUNTIME)
    assert 'MIN_INTERVAL_HOURS = 4' in control
    assert '"factory": {' in control
    assert '"default_hours": 6' in control
    assert '"seven_agent": {' in control
    assert '"default_hours": 168' in control
    assert 'time.sleep(300)' in runtime
    assert 'minimum_report_interval=4h' in runtime


def test_central_runtime_has_no_github_workflow_dispatch_side_effects():
    text = _text(CENTRAL)
    assert 'gh workflow run' not in text
    assert 'gh pr list' not in text
    assert 'subprocess.run' not in text
    assert 'actions: write' not in text
