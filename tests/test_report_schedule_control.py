from pathlib import Path
from types import SimpleNamespace

from learnerbot import report_schedule_control as sched


def app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def test_defaults_match_operating_model(tmp_path):
    a = app(tmp_path)
    assert sched.load_schedule(a) == {
        "trade": 4,
        "engineering": 48,
        "strategy": 48,
        "factory": 6,
        "engineering_ai": 48,
        "seven_agent": 168,
    }


def test_master_cannot_set_automatic_cycle_below_four_hours(tmp_path):
    a = app(tmp_path)
    try:
        sched.set_interval(a, "factory", 3, changed_by="test")
    except ValueError as exc:
        assert "less than 4 hours" in str(exc)
    else:
        raise AssertionError("sub-4h interval accepted")


def test_master_can_change_and_view_frequency(tmp_path):
    a = app(tmp_path)
    sched.set_interval(a, "strategy", 72, changed_by="master")
    snap = sched.snapshot(a, now=1000)
    row = next(x for x in snap["reports"] if x["key"] == "strategy")
    assert row["hours"] == 72


def test_failed_automatic_attempt_is_not_retried_inside_interval(tmp_path):
    a = app(tmp_path)
    sched.load_state(a, now=100)
    sched.mark_attempt(a, "trade", manual=False, now=100 + 4 * 3600)
    sched.mark_result(a, "trade", success=False, detail="x", now=100 + 4 * 3600 + 1)
    assert "trade" not in sched.due_reports(a, now=100 + 4 * 3600 + 60)


def test_runtime_scheduler_master_command_contract():
    patch = Path("learnerbot/telegram_report_schedule_patch.py").read_text(encoding="utf-8")
    assert '("aireports"' in patch
    assert '("aifrequency"' in patch
    assert '("airun"' in patch
    assert "time.sleep(300)" in patch
    assert "minimum_report_interval=4h" in patch


def test_factory_and_rotation_contracts_are_centralised():
    worker = Path("scripts/central_report_scheduler.py").read_text(encoding="utf-8")
    assert 'ops._panel_for = lambda package: list(AGENTS)' in worker
    assert 'NON_GPT_REVIEWERS' in worker
    assert 'ops._ask("gpt", gpt_prompt' in worker
    assert '"STRATEGY_FACTORY_REVIEW"' in worker
    assert '"trade-strategy-economics"' in worker
