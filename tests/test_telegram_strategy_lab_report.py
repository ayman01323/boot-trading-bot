from pathlib import Path
from types import SimpleNamespace

from learnerbot import strategy_lab as lab
from learnerbot import telegram_strategy_lab_report_patch as report_patch


def _app(tmp_path: Path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")


def test_strategy_lab_page_renders_registered_strategy_with_no_activity(tmp_path):
    app = _app(tmp_path)
    lab.register_strategy(
        app, name="Test Strategy", family="TEST_FAMILY", source="OPERATOR",
        hypothesis="A test hypothesis.",
    )
    page = report_patch.strategy_lab_page(app)
    assert "STRATEGY LAB" in page
    assert "Test Strategy" in page
    assert "No recorded activity yet" in page


def test_strategy_lab_page_renders_real_metrics(tmp_path):
    app = _app(tmp_path)
    reg = lab.register_strategy(
        app, name="Winning Strategy", family="TEST_FAMILY", source="MARKET_NATIVE",
        hypothesis="A test hypothesis with real trades.",
    )
    sid = reg["strategy_id"]
    for i in range(3):
        lab.record_window(
            app, sid, window_start=1000 + i * 3600, window_end=1000 + (i + 1) * 3600, mode="LIVE",
            opportunities=5, eligible_opportunities=5, trades=5, wins=4, losses=1,
            gross_profit="2.0", gross_loss="0.5",
        )
    page = report_patch.strategy_lab_page(app)
    assert "Winning Strategy" in page
    assert "trades=15/15" in page
    assert "wins=12 losses=3" in page


def test_handle_update_requires_master(tmp_path, monkeypatch):
    from learnerbot import telegram_ui as ui

    app = _app(tmp_path)
    sent = []
    monkeypatch.setattr(ui, "_send", lambda app_, tid, text, kb=None: sent.append(text))
    monkeypatch.setattr(ui, "_require_master", lambda app_, tid: (_ for _ in ()).throw(ValueError("Master/admin permission required for this command")))

    update = {"message": {"chat": {"id": 123}, "text": "/strategylab"}}
    report_patch.handle_update(app, update)
    assert sent and "permission required" in sent[0]
