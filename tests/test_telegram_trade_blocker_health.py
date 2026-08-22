from types import SimpleNamespace

from learnerbot import telegram_trade_blocker_health_patch as patch


def _sample_snapshot():
    return {
        "generated_epoch": 1,
        "etherscan_configured": False,
        "polygon_focus": True,
        "platform_auto": True,
        "evm": {
            "polygon": {
                "leaders": 0,
                "status_wallets": 1007,
                "complete": 0,
                "errors": 1007,
                "newest": 0,
                "dominant": "RuntimeError: ETHERSCAN_API_KEY is not configured",
            }
        },
        "fast_market": {
            "status": "OK",
            "updated": 0,
            "routes": 25,
            "merged": 7,
            "eligible": 0,
            "auto_events": 0,
            "simulations": 0,
            "simulation_reason": "",
            "executions": 0,
        },
        "solana": {
            "engine_enabled": True,
            "live_enabled": True,
            "leaders": 1,
            "events": 0,
            "counts": {},
            "rows": [],
        },
    }


def test_report_explains_each_major_no_trade_layer(monkeypatch):
    monkeypatch.setattr(patch, "_snapshot", lambda app, tid: _sample_snapshot())
    text = patch.build_report(SimpleNamespace(), "master")
    assert "ETHERSCAN_API_KEY MISSING" in text
    assert "POLYGON" in text and "errors 1007" in text
    assert "scope <b>Polygon only</b>" in text
    assert "routes 25" in text and "eligible 0" in text
    assert "no fresh selected-leader swap" in text
    assert "does not weaken profit, liquidity, simulation" in text


def test_top_reason_returns_most_common_reason():
    rows = [
        {"reason": "profit floor"},
        {"reason": "gas floor"},
        {"reason": "profit floor"},
    ]
    assert patch._top_reason(rows) == "profit floor"


def test_startup_health_never_writes_secret_value(tmp_path, monkeypatch):
    app = SimpleNamespace(
        csv_dir=tmp_path / "csv",
        data_dir=tmp_path / "data",
        etherscan_api_key="do-not-write-this-secret",
        telegram_bot_token="",
    )
    app.csv_dir.mkdir()
    monkeypatch.setattr(patch, "all_users", lambda *args, **kwargs: [])
    monkeypatch.setattr(patch._polygon, "focus_enabled", lambda app: False)
    patch._publish_startup_health(app)
    raw = (app.data_dir / "trade_blocker_health.json").read_text(encoding="utf-8")
    assert "do-not-write-this-secret" not in raw
    assert '"etherscan_configured": true' in raw
