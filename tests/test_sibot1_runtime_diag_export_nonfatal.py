from pathlib import Path

from learnerbot import sibot1_runtime_diag_export_patch as diag


class _App:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


def test_runtime_diag_falls_back_when_primary_output_is_unwritable(monkeypatch, tmp_path):
    app = _App(tmp_path / 'runtime-data')
    primary = tmp_path / 'foreign-owned-runtime-diag.json'
    fallback = app.data_dir / 'sibot1' / 'runtime_diag.json'
    calls = []

    monkeypatch.setattr(diag, 'OUT', primary)
    monkeypatch.setattr(diag, '_ACTIVE_OUT', primary)
    monkeypatch.setattr(diag, 'snapshot', lambda _app: {'redacted': True})

    def fake_atomic_write(path, data):
        calls.append((path, data))
        if path == primary:
            raise PermissionError(1, 'Operation not permitted', str(primary))
        assert path == fallback

    monkeypatch.setattr(diag, '_atomic_write', fake_atomic_write)

    actual = diag._write(app)

    assert actual == fallback
    assert diag._ACTIVE_OUT == fallback
    assert [path for path, _ in calls] == [primary, fallback]


def test_runtime_diag_startup_failure_never_terminates_bot(monkeypatch, tmp_path):
    app = _App(tmp_path / 'runtime-data')
    thread_started = {'value': False}

    monkeypatch.setattr(diag, '_STARTED', False)
    monkeypatch.setattr(diag, '_ACTIVE_OUT', diag.OUT)

    def fail_write(_app):
        raise PermissionError(1, 'Operation not permitted', '/var/tmp/sibot1-runtime-diag.json')

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            thread_started['value'] = True

    monkeypatch.setattr(diag, '_write', fail_write)
    monkeypatch.setattr(diag.threading, 'Thread', FakeThread)

    diag._start(app)

    assert diag._STARTED is True
    assert thread_started['value'] is True
