from types import SimpleNamespace

from learnerbot import telegram_solana_force_exit_patch as report_patch
from learnerbot import telegram_ui as ui


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")


def test_requires_confirm_keyword(tmp_path, monkeypatch):
    app = _app(tmp_path)
    sent = []
    monkeypatch.setattr(ui, "_auth", lambda app_, tid: True)
    monkeypatch.setattr(ui, "_send", lambda app_, tid, text, kb=None: sent.append(text))

    update = {"message": {"chat": {"id": 1}, "text": "/solanaforceexit p1"}}
    report_patch.handle_update(app, update)
    assert sent and "CONFIRM" in sent[0]


def test_requires_auth(tmp_path, monkeypatch):
    app = _app(tmp_path)
    sent = []
    monkeypatch.setattr(ui, "_auth", lambda app_, tid: False)
    monkeypatch.setattr(ui, "_send", lambda app_, tid, text, kb=None: sent.append(text))

    update = {"message": {"chat": {"id": 1}, "text": "/solanaforceexit p1 CONFIRM"}}
    report_patch.handle_update(app, update)
    assert sent and "Not authorised" in sent[0]


def test_calls_force_close_and_reports_result(tmp_path, monkeypatch):
    app = _app(tmp_path)
    sent = []
    calls = []
    monkeypatch.setattr(ui, "_auth", lambda app_, tid: True)
    monkeypatch.setattr(ui, "_send", lambda app_, tid, text, kb=None: sent.append(text))

    def fake_force_close(app_, tid, position_id):
        calls.append((tid, position_id))
        return {"closed": True, "liquidity_adaptive_fraction": "1", "net_sol": "-0.42"}

    from learnerbot import solana_emergency_liquidity_unwind_patch as unwind
    monkeypatch.setattr(unwind, "force_close_live_position", fake_force_close)

    update = {"message": {"chat": {"id": 42}, "text": "/solanaforceexit p9 CONFIRM"}}
    report_patch.handle_update(app, update)

    assert calls == [("42", "p9")] or calls == [(42, "p9")]
    assert sent and "Forced Solana exit executed" in sent[0]
    assert "-0.42" in sent[0]


def test_surfaces_error_from_force_close(tmp_path, monkeypatch):
    app = _app(tmp_path)
    sent = []
    monkeypatch.setattr(ui, "_auth", lambda app_, tid: True)
    monkeypatch.setattr(ui, "_send", lambda app_, tid, text, kb=None: sent.append(text))

    def fake_force_close(app_, tid, position_id):
        raise ValueError("This position does not belong to this account")

    from learnerbot import solana_emergency_liquidity_unwind_patch as unwind
    monkeypatch.setattr(unwind, "force_close_live_position", fake_force_close)

    update = {"message": {"chat": {"id": 1}, "text": "/solanaforceexit p1 CONFIRM"}}
    report_patch.handle_update(app, update)
    assert sent and "does not belong" in sent[0]
