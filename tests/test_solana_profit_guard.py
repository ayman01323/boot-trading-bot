import subprocess
import sys
import textwrap


def _run(code: str):
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], text=True, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_solana_profit_factor_recent_and_drawdown_gates_in_isolated_process():
    _run(r'''
        from decimal import Decimal
        from learnerbot import solana_profit_guard_patch as p
        cfg={k:v[0] for k,v in p._SOL_QUALITY_DEFAULTS.items()}
        cfg.update({"min_closed_trades":"10","min_win_rate_pct":"55","require_complete_history":"false"})
        good={
            "closed":20,"win_rate":Decimal("65"),"profit_factor":Decimal("2"),"drawdown_pct":Decimal("8"),
            "recent_win_rate":Decimal("65"),"recent_profit_factor":Decimal("1.8"),"net":Decimal("1"),"history_complete":False,
        }
        assert p._historical_ok(good,cfg)
        weak=dict(good); weak["profit_factor"]=Decimal("1.2")
        assert not p._historical_ok(weak,cfg)
        weak=dict(good); weak["drawdown_pct"]=Decimal("25")
        assert not p._historical_ok(weak,cfg)
        weak=dict(good); weak["recent_win_rate"]=Decimal("45")
        assert not p._historical_ok(weak,cfg)
        cfg["require_complete_history"]="true"
        assert not p._historical_ok(good,cfg)
    ''')


def test_solana_quality_page_shows_profit_guards_in_isolated_process():
    _run(r'''
        from learnerbot import telegram_solana_quality_settings_patch as ui
        ui._PREV_PAGE=lambda app,tid:"<b>BASE SOLANA PAGE</b>"
        ui._sol.settings=lambda app:{
            "min_closed_trades":"10","min_win_rate_pct":"55","min_profit_factor":"1.5","max_leader_drawdown_pct":"20",
            "recent_trade_window":"20","min_recent_win_rate_pct":"55","min_recent_profit_factor":"1.10","require_complete_history":"false",
            "max_signal_age_seconds":"20","max_entry_deterioration_pct":"1.5","max_roundtrip_loss_pct":"2","stop_loss_pct":"10",
            "take_profit_pct":"25","break_even_trigger_pct":"5","break_even_floor_pct":"0.25","trailing_trigger_pct":"10",
            "trailing_gap_pct":"4","leader_exit_loss_cap_pct":"2","min_copied_trades_for_guard":"3","min_copied_win_rate_pct":"40",
            "min_copied_profit_factor":"1.0","max_consecutive_copied_losses":"3","leader_suspend_minutes":"360",
        }
        text=ui.solana_page(object(),"1")
        assert "Profit factor <b>1.5x+" in text
        assert "max drawdown <b>20%" in text
        assert "Recent 20" in text
        assert "Actual LIVE-copy guard" in text
        assert "Complete history: <b>optional" in text
    ''')
