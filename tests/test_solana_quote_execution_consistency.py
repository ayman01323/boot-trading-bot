from types import SimpleNamespace

from learnerbot import solana_quote_execution_consistency_patch as quote


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"outAmount": "123"}


def test_preflight_excludes_same_router_as_live_executor(monkeypatch, tmp_path):
    app = SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(params or {})
        return _Response()

    monkeypatch.setattr(quote._sol.requests, "get", fake_get)
    monkeypatch.setattr(quote._sol, "_jupiter_throttle", lambda api_key: None)

    result = quote.jupiter_quote_executable(app, "in", "out", 500000)
    assert result["outAmount"] == "123"
    assert captured["excludeRouters"] == "jupiterz"
    assert captured["amount"] == "500000"
