import subprocess
import sys
import textwrap


def _run(code: str):
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], text=True, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_profit_guard_quality_and_entry_circuit_breakers_in_isolated_process():
    _run(r'''
        from decimal import Decimal
        from types import SimpleNamespace
        from learnerbot import sibot_profit_guard_patch as p

        cfg = {k:v[0] for k,v in p._QUALITY_DEFAULTS.items()}
        cfg.update({"require_complete_history":"true","min_closed_trades":"50","min_win_rate_pct":"55","lookback_days":"60","max_roundtrip_loss_pct":"2"})
        good = {
            "closed":80,"wins":52,"win_rate":Decimal("65"),"profit":Decimal("2"),"loss":Decimal("0.8"),"net":Decimal("1.2"),
            "profit_factor":Decimal("2.5"),"drawdown_pct":Decimal("8"),"avg_return_pct":Decimal("6"),"recent_closed":20,
            "recent_win_rate":Decimal("70"),"recent_profit_factor":Decimal("2"),"recent_avg_return_pct":Decimal("7"),"history_complete":True,
        }
        ok,reason=p._leader_quality_ok(good,cfg)
        assert ok and reason=="PASS"
        weak=dict(good); weak["profit_factor"]=Decimal("1.2")
        ok,reason=p._leader_quality_ok(weak,cfg)
        assert not ok and "profit factor" in reason
        strong=p._quality_score(good,cfg)
        weaker=dict(good); weaker["recent_win_rate"]=Decimal("55"); weaker["recent_profit_factor"]=Decimal("1.1")
        assert strong > p._quality_score(weaker,cfg)

        trader=SimpleNamespace(telegram_id="1",chain=SimpleNamespace(chain_id=56))
        event={"leader_wallet":"0x"+"1"*40,"token":"0x"+"2"*40}
        p.quality_metrics=lambda *a,**k: good
        p._copied_metrics=lambda *a,**k: {"closed":0,"win_rate":Decimal(0),"profit_factor":Decimal(0),"consecutive_losses":0,"latest_closed_at":0}
        p._suspension_status=lambda *a,**k: (False,"PASS")
        p._chain_risk=lambda *a,**k: {"daily_pct":Decimal("-5"),"drawdown_pct":Decimal("2")}
        called={"n":0}
        def prev(*a,**k):
            called["n"]+=1
            return True,"PASS",{}
        p._PREV_VALIDATE_ENTRY=prev
        ok,reason,_=p._validate_entry(None,trader,event,Decimal("1"),cfg,True)
        assert not ok and "daily chain loss" in reason and called["n"]==0

        m=dict(good); m["avg_return_pct"]=Decimal("2"); m["recent_avg_return_pct"]=Decimal("2")
        p.quality_metrics=lambda *a,**k: m
        p._chain_risk=lambda *a,**k: {"daily_pct":Decimal(0),"drawdown_pct":Decimal(0)}
        p._sibot._estimated_gas_native=lambda *a,**k: Decimal("0.0025")
        p._PREV_VALIDATE_ENTRY=lambda *a,**k: (True,"PASS",{"roundtrip_loss_pct":Decimal("1")})
        ok,reason,check=p._validate_entry(None,trader,event,Decimal("1"),cfg,True)
        assert not ok and "does not cover" in reason
        assert check["expected_edge_pct"]==Decimal("2")
    ''')


def test_quality_settings_keyboard_exposes_new_guards_in_isolated_process():
    _run(r'''
        from learnerbot import sibot_profit_guard_patch as p
        from learnerbot import telegram_sibot_quality_settings_patch as ui
        cfg={k:v[0] for k,v in p._QUALITY_DEFAULTS.items()}
        cfg.update({
            "lookback_days":"60","leaders_per_chain":"3","allocation_pct":"15","max_exposure_pct":"60","min_closed_trades":"50",
            "min_win_rate_pct":"55","max_signal_age_seconds":"20","max_entry_deterioration_pct":"1.5","max_roundtrip_loss_pct":"2",
            "stop_loss_pct":"10","take_profit_pct":"25","break_even_trigger_pct":"5","break_even_floor_pct":"0.25",
            "trailing_trigger_pct":"10","trailing_gap_pct":"4","leader_exit_loss_cap_pct":"2","min_exit_profit_pct":"0.10",
            "max_positions_per_chain":"5","max_hold_hours":"24","mirror_partial_sells":"true","require_complete_history":"true",
        })
        ui._sibot.user_settings=lambda *a,**k: cfg
        kb=ui.settings_keyboard(object(),"1")
        callbacks=[b["callback_data"] for row in kb["inline_keyboard"] for b in row if b.get("callback_data","").startswith("sibot:set:")]
        assert "sibot:set:min_profit_factor" in callbacks
        assert "sibot:set:daily_loss_limit_pct" in callbacks
        assert "sibot:set:dynamic_max_allocation_pct" in callbacks
        assert "sibot:set:edge_cost_multiple" in callbacks
    ''')


def test_profit_guard_migration_skips_lightweight_app_without_data_dir():
    _run(r'''
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from learnerbot import sibot_profit_guard_patch as guard
        from learnerbot import sibot_profit_guard_runtime_compat_patch  # noqa: F401
        with tempfile.TemporaryDirectory() as td:
            app=SimpleNamespace(csv_dir=Path(td))
            path=guard._sibot.ensure_settings(app)
            assert path.exists()
            assert not hasattr(app,"data_dir")
    ''')
