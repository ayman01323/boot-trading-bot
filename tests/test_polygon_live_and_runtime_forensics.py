from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace


def _kv(path: Path) -> dict[tuple[str, str], str]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {
            (str(row.get("chain_id") or ""), str(row.get("setting") or "")): str(row.get("value") or "")
            for row in csv.DictReader(fh)
        }


def test_polygon_live_migration_is_polygon_scoped_and_keeps_cross_dex_shadow(monkeypatch, tmp_path):
    from learnerbot import polygon_live_enable_migration as mod

    app = SimpleNamespace(csv_dir=tmp_path / "csv", data_dir=tmp_path / "data")
    app.csv_dir.mkdir(parents=True)
    app.data_dir.mkdir(parents=True)

    monkeypatch.setattr(
        mod,
        "load_chains",
        lambda app, enabled_only=False: [
            SimpleNamespace(chain_id=137, slug="polygon", enabled=True, type="EVM")
        ],
    )
    monkeypatch.setattr(
        mod,
        "load_dex_registry",
        lambda csv_dir, chain_id: [
            {
                "dex_name": "QuickSwap",
                "version": "V2",
                "enabled": "true",
                "auto_execute": "true",
            }
        ],
    )
    user_calls = []
    focus_calls = []
    monkeypatch.setattr(mod, "set_user_setting", lambda *a, **kw: user_calls.append((a, kw)))
    monkeypatch.setattr(mod, "set_focus", lambda app, enabled: focus_calls.append(bool(enabled)))

    mod.enable_polygon_live(app)

    auto = _kv(app.csv_dir / "auto_trading_settings.csv")
    live = _kv(app.csv_dir / "live_trading_settings.csv")
    assert auto[("*", "auto_trading_enabled")] == "true"
    assert live[("137", "trading_enabled")] == "true"
    assert focus_calls == [True]

    settings = {(kw["chain_id"], a[2], a[3]) for a, kw in user_calls}
    assert ("137", "live_trading_enabled", "true") in settings
    assert ("137", "auto_trading_enabled", "true") in settings
    assert ("137", "recommendation_mode", "ARMED") in settings
    assert ("137", "sibot_auto_trade_enabled", "true") in settings

    marker = (app.data_dir / mod.MARKER).read_text(encoding="utf-8")
    assert "polygon_live=true" in marker
    assert "direct_auto_focus=polygon_only" in marker
    assert "route_scope=single_router_profit_protected_cycles" in marker
    assert "cross_dex_live=false" in marker


def test_runtime_forensics_bridge_survives_git_publish_failure(monkeypatch, tmp_path):
    from learnerbot import loss_forensics_runtime_bridge_patch as mod

    bridge = tmp_path / "latest_loss_forensics.json"
    report = {
        "schema_version": 1,
        "generated_epoch": 1234567890,
        "generated_utc": "2009-02-13 23:31:30 UTC",
        "privacy": "sanitised test evidence",
        "solana_live": {"available": True},
    }
    monkeypatch.setattr(mod, "BRIDGE_PATH", bridge)
    monkeypatch.setattr(
        mod,
        "_PREV_PUBLISH",
        lambda app, zip_path, gpt_result=None: {
            "ok": False,
            "error": "read-only deploy key",
            "report": report,
        },
    )

    result = mod.publish_loss_forensics_with_runtime_bridge(object(), "audit.zip", None)
    assert result["ok"] is False
    assert result["runtime_bridge_ok"] is True
    assert Path(result["runtime_bridge"]) == bridge
    assert json.loads(bridge.read_text(encoding="utf-8")) == report


def test_evm_copy_engine_has_solana_equivalent_strategy_controls():
    from learnerbot import sibot
    from learnerbot import sibot_profit_guard_patch as guard

    base_controls = {
        "lookback_days",
        "leaders_per_chain",
        "min_closed_trades",
        "min_win_rate_pct",
        "max_signal_age_seconds",
        "max_entry_deterioration_pct",
        "max_roundtrip_loss_pct",
        "stop_loss_pct",
        "take_profit_pct",
        "max_hold_hours",
        "mirror_partial_sells",
    }
    quality_controls = {
        "min_profit_factor",
        "recent_trade_window",
        "min_recent_win_rate_pct",
        "min_recent_profit_factor",
        "max_leader_drawdown_pct",
        "min_copied_trades_for_guard",
        "min_copied_win_rate_pct",
        "min_copied_profit_factor",
        "max_consecutive_copied_losses",
        "min_expected_edge_pct",
        "edge_cost_multiple",
        "daily_loss_limit_pct",
        "portfolio_drawdown_limit_pct",
    }

    assert base_controls.issubset(sibot.DEFAULTS)
    assert quality_controls.issubset(guard._QUALITY_DEFAULTS)
