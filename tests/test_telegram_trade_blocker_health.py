from types import SimpleNamespace

from learnerbot import telegram_trade_blocker_health_patch as patch
from learnerbot import trading_pipeline_observability_patch as obs


def _sample_snapshot():
    return {
        "generated_epoch": 1,
        "etherscan_configured": False,
        "polygon_focus": True,
        "platform_auto": True,
        "platform_live": True,
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


def _sample_pipeline_snapshot(*, platform_live=True):
    polygon_reason = (
        "current routes fail scanner/quarantine/product-policy eligibility"
        if platform_live
        else "platform LIVE off"
    )
    return {
        "schema_version": 1,
        "generated_epoch": 1,
        "evm_sibot": {
            "chains": {
                "bsc": {
                    "history_wallets": 1007,
                    "history_complete": 1007,
                    "history_newest": 1,
                    "reconstructed_trades": 0,
                    "wallets_with_trades": 0,
                    "pool": 0,
                    "qualified": 0,
                    "selected": 0,
                    "first_zero_stage": "reconstructed",
                    "latest_reconstruction": {
                        "transfer_rows": 25,
                        "router_txs": 7,
                        "buys": 0,
                        "sells": 0,
                        "matched_closed": 0,
                        "diagnostic_reason": "legacy RuntimeError: ETHERSCAN_API_KEY is not configured",
                    },
                    "backlog": {},
                }
            },
            "first_zero_stage": "bsc:reconstructed",
        },
        "polygon_auto": {
            "scanned_routes": 25,
            "economically_eligible": 0,
            "ready_users": 0,
            "auto_live_ready": 0,
            "simulated": 0,
            "route_found": 0,
            "submission_attempted": 0,
            "broadcast": 0,
            "filled": 0,
            "platform_auto": True,
            "platform_live": platform_live,
            "scanner_status": "OK",
            "scanner_updated_epoch": 1,
            "first_zero_stage": "economically eligible" if platform_live else "AUTO/LIVE ready",
            "first_rejection_reason": polygon_reason,
        },
        "solana": {
            "discovered": 45,
            "reconstructed": 20,
            "positive_pool": 45,
            "quality_qualified": 0,
            "selected": 0,
            "preflight_decisions_24h": 0,
            "copied_buys_24h": 0,
            "first_failure_counts": {"historical win rate below minimum": 42},
            "zero_qualified_streak": 3,
            "research_needed": True,
            "first_zero_stage": "quality-qualified",
        },
        "safety_gates_unchanged": True,
        "shadow_reconstruction_only": True,
    }


def test_report_explains_each_major_no_trade_layer(monkeypatch):
    monkeypatch.setattr(obs, "snapshot", lambda app, tid: _sample_pipeline_snapshot())
    monkeypatch.setattr(obs, "_compact_backlog_line", lambda app: "collecting")
    text = obs.build_report(SimpleNamespace(), "master")
    assert "MASTER TRADING DIAGNOSTIC" in text
    assert "BSC: history <b>1007/1007</b>" in text
    assert "Polygon AUTO" in text and "economically eligible" in text
    assert "Solana: discovered" in text and "quality-qualified" in text
    assert "Strategy Factory leader-source research trigger" in text
    assert "Existing profit, quality, liquidity, simulation" in text


def test_report_shows_legacy_etherscan_error_as_recorded_diagnostic_only(monkeypatch):
    monkeypatch.setattr(obs, "snapshot", lambda app, tid: _sample_pipeline_snapshot())
    monkeypatch.setattr(obs, "_compact_backlog_line", lambda app: "collecting")
    text = obs.build_report(SimpleNamespace(), "master")
    assert "legacy RuntimeError: ETHERSCAN_API_KEY is not configured" in text
    assert "ETHERSCAN_API_KEY MISSING" not in text


def test_report_shows_platform_live_off_as_current_polygon_gate(monkeypatch):
    monkeypatch.setattr(obs, "snapshot", lambda app, tid: _sample_pipeline_snapshot(platform_live=False))
    monkeypatch.setattr(obs, "_compact_backlog_line", lambda app: "collecting")
    text = obs.build_report(SimpleNamespace(), "master")
    assert "platform LIVE off" in text
    assert "AUTO/LIVE ready" in text


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
    monkeypatch.setattr(obs, "_start_background", lambda app: None)
    monkeypatch.setattr(obs, "all_users", lambda *args, **kwargs: [])
    monkeypatch.setattr(obs, "snapshot", lambda app, tid: _sample_pipeline_snapshot())
    obs._publish_startup_health(app)
    raw = (app.data_dir / "trade_blocker_health.json").read_text(encoding="utf-8")
    assert "do-not-write-this-secret" not in raw
    assert '"safety_gates_unchanged": true' in raw
    assert '"shadow_reconstruction_only": true' in raw
    assert '"etherscan_configured"' not in raw


def _gate_app(tmp_path, *, telegram_bot_token="test-token"):
    app = SimpleNamespace(data_dir=tmp_path / "data", telegram_bot_token=telegram_bot_token)
    return app


def test_alert_platform_gate_off_warns_master_for_auto_only(tmp_path, monkeypatch):
    app = _gate_app(tmp_path)
    sent = []
    monkeypatch.setattr(patch, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))
    patch._maybe_alert_platform_gate_off(app, ["555"], {"platform_auto": False, "platform_live": True})
    assert len(sent) == 1
    assert sent[0][0] == "555"
    assert "AUTO (execution)" in sent[0][1]
    assert "LIVE (signing)" not in sent[0][1]
    assert (app.data_dir / ".platform_gate_off_warning_epoch").exists()


def test_alert_platform_gate_off_is_throttled(tmp_path, monkeypatch):
    app = _gate_app(tmp_path)
    sent = []
    monkeypatch.setattr(patch, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))
    safe = {"platform_auto": False, "platform_live": False}
    patch._maybe_alert_platform_gate_off(app, ["555"], safe)
    patch._maybe_alert_platform_gate_off(app, ["555"], safe)
    assert len(sent) == 1
    assert "LIVE (signing)" in sent[0][1] and "AUTO (execution)" in sent[0][1]


def test_alert_platform_gate_off_skipped_when_gates_on(tmp_path, monkeypatch):
    app = _gate_app(tmp_path)
    sent = []
    monkeypatch.setattr(patch, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))
    patch._maybe_alert_platform_gate_off(app, ["555"], {"platform_auto": True, "platform_live": True})
    assert sent == []
    assert not (app.data_dir / ".platform_gate_off_warning_epoch").exists()


def test_alert_platform_gate_off_skipped_without_telegram_token(tmp_path, monkeypatch):
    app = _gate_app(tmp_path, telegram_bot_token="")
    sent = []
    monkeypatch.setattr(patch, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))
    patch._maybe_alert_platform_gate_off(app, ["555"], {"platform_auto": False, "platform_live": False})
    assert sent == []


def test_alert_platform_gate_off_skipped_when_snapshot_unknown(tmp_path, monkeypatch):
    # A snapshot that could not be computed (e.g. no master registered yet) must
    # not be mistaken for a confirmed OFF gate.
    app = _gate_app(tmp_path)
    sent = []
    monkeypatch.setattr(patch, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))
    patch._maybe_alert_platform_gate_off(app, ["555"], {})
    assert sent == []
