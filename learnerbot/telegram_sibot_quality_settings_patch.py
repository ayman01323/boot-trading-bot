from __future__ import annotations

import html

from . import sibot as _sibot
from . import telegram_sibot_patch as _tg

DIV = _tg.DIV


def _v(app, tid, key, default="-"):
    return html.escape(str(_sibot.setting_value(app, tid, key) or default))


def settings_page(app, tid):
    c = _sibot.user_settings(app, tid, 0)
    return "\n".join([
        "<b>⚙️ SiBot Quality & Profit Settings</b>",
        DIV,
        "<b>💰 CAPITAL</b>",
        f"Per-entry base: <b>{_v(app,tid,'allocation_pct','15')}%</b>  •  max exposure: <b>{_v(app,tid,'max_exposure_pct','60')}%</b>",
        f"Dynamic size: <b>{_v(app,tid,'dynamic_min_allocation_pct','5')}–{_v(app,tid,'dynamic_max_allocation_pct','20')}%</b>  •  one-leader cap: <b>{_v(app,tid,'max_leader_exposure_pct','30')}%</b>",
        "",
        "<b>🏆 LEADER QUALITY</b>",
        f"History: <b>{_v(app,tid,'lookback_days','60')}d</b>  •  leaders: <b>{_v(app,tid,'leaders_per_chain','3')}/chain</b>",
        f"Closed trades: <b>{_v(app,tid,'min_closed_trades','50')}+</b>  •  win: <b>{_v(app,tid,'min_win_rate_pct','55')}%+</b>",
        f"Profit factor: <b>{_v(app,tid,'min_profit_factor','1.5')}x+</b>  •  max drawdown: <b>{_v(app,tid,'max_leader_drawdown_pct','20')}%</b>",
        f"Recent {_v(app,tid,'recent_trade_window','20')} trades: win <b>{_v(app,tid,'min_recent_win_rate_pct','55')}%+</b>  •  PF <b>{_v(app,tid,'min_recent_profit_factor','1.1')}x+</b>",
        f"Recent weighting: <b>{_v(app,tid,'recent_weight_pct','40')}%</b>  •  complete history: <b>{'REQUIRED' if _sibot._bool(c.get('require_complete_history'),True) else 'optional'}</b>",
        "",
        "<b>🎯 ENTRY QUALITY</b>",
        f"Signal age: <b>≤ {_v(app,tid,'max_signal_age_seconds','20')}s</b>  •  worse entry: <b>≤ {_v(app,tid,'max_entry_deterioration_pct','1.5')}%</b>",
        f"Immediate round trip: <b>≤ {_v(app,tid,'max_roundtrip_loss_pct','2')}%</b>  •  est. gas: <b>≤ {_v(app,tid,'max_gas_cost_pct','2')}%</b>",
        f"Expected edge: <b>≥ {_v(app,tid,'min_expected_edge_pct','1')}%</b> and <b>≥ {_v(app,tid,'edge_cost_multiple','2')}×</b> estimated cost",
        "",
        "<b>📊 ACTUAL COPIED PERFORMANCE</b>",
        f"After {_v(app,tid,'min_copied_trades_for_guard','3')} copied closes: win <b>≥ {_v(app,tid,'min_copied_win_rate_pct','40')}%</b>, PF <b>≥ {_v(app,tid,'min_copied_profit_factor','1')}x</b>",
        f"Suspend after <b>{_v(app,tid,'max_consecutive_copied_losses','3')}</b> consecutive losses for <b>{_v(app,tid,'leader_suspend_minutes','360')} min</b>",
        "",
        "<b>🛡 EXIT & CIRCUIT BREAKERS</b>",
        f"Stop <b>{_v(app,tid,'stop_loss_pct','10')}%</b>  •  take <b>{_v(app,tid,'take_profit_pct','25')}%</b>  •  max hold <b>{_v(app,tid,'max_hold_hours','24')}h</b>",
        f"Break-even trigger <b>{_v(app,tid,'break_even_trigger_pct','5')}%</b> / floor <b>{_v(app,tid,'break_even_floor_pct','0.25')}%</b>",
        f"Trailing trigger <b>{_v(app,tid,'trailing_trigger_pct','10')}%</b> / gap <b>{_v(app,tid,'trailing_gap_pct','4')}%</b>",
        f"Leader-exit loss cap <b>{_v(app,tid,'leader_exit_loss_cap_pct','2')}%</b>  •  min exit profit <b>{_v(app,tid,'min_exit_profit_pct','0.10')}%</b>",
        f"Daily-loss pause <b>{_v(app,tid,'daily_loss_limit_pct','4')}%</b>  •  realised drawdown pause <b>{_v(app,tid,'portfolio_drawdown_limit_pct','12')}%</b>",
        "",
        "<b>🔒 PROFIT LOCK / DIVERSIFICATION</b>",
        f"After daily +{_v(app,tid,'profit_lock_trigger_pct','5')}%: new size × <b>{_v(app,tid,'profit_lock_size_multiplier_pct','70')}%</b>",
        f"Consensus window <b>{_v(app,tid,'leader_consensus_window_seconds','120')}s</b>  •  bonus up to <b>{_v(app,tid,'leader_consensus_bonus_pct','15')}%</b>",
        f"Max positions: <b>{_v(app,tid,'max_positions_per_chain','5')}/chain</b>  •  partial leader sells: <b>{'ON' if _sibot._bool(c.get('mirror_partial_sells'),True) else 'OFF'}</b>",
        "",
        "<i>These settings can only make selection stricter, reduce sizing, or pause new entries. Existing LIVE activation/signing gates remain separate.</i>",
    ])


def settings_keyboard(app, tid):
    c = _sibot.user_settings(app, tid, 0)
    partial = _sibot._bool(c.get("mirror_partial_sells"), True)
    def b(label, key):
        return {"text": label, "callback_data": f"sibot:set:{key}"}
    rows = [
        [b(f"🗓 History {c.get('lookback_days','60')}d", "lookback_days"), b(f"🏆 Leaders {c.get('leaders_per_chain','3')}", "leaders_per_chain")],
        [b(f"💵 Base buy {c.get('allocation_pct','15')}%", "allocation_pct"), b(f"🧱 Exposure {c.get('max_exposure_pct','60')}%", "max_exposure_pct")],
        [b(f"✅ Trades {c.get('min_closed_trades','50')}+", "min_closed_trades"), b(f"🎯 Win {c.get('min_win_rate_pct','55')}%+", "min_win_rate_pct")],
        [b(f"📈 PF {c.get('min_profit_factor','1.5')}x+", "min_profit_factor"), b(f"📉 DD ≤{c.get('max_leader_drawdown_pct','20')}%", "max_leader_drawdown_pct")],
        [b(f"🕘 Recent {c.get('recent_trade_window','20')}", "recent_trade_window"), b(f"🎯 Recent win {c.get('min_recent_win_rate_pct','55')}%", "min_recent_win_rate_pct")],
        [b(f"📈 Recent PF {c.get('min_recent_profit_factor','1.1')}x", "min_recent_profit_factor"), b(f"⚖️ Recent weight {c.get('recent_weight_pct','40')}%", "recent_weight_pct")],
        [b(f"⏱ Signal {c.get('max_signal_age_seconds','20')}s", "max_signal_age_seconds"), b(f"📉 Entry +{c.get('max_entry_deterioration_pct','1.5')}%", "max_entry_deterioration_pct")],
        [b(f"🔁 Round trip {c.get('max_roundtrip_loss_pct','2')}%", "max_roundtrip_loss_pct"), b(f"⛽ Gas {c.get('max_gas_cost_pct','2')}%", "max_gas_cost_pct")],
        [b(f"📐 Edge {c.get('min_expected_edge_pct','1')}%", "min_expected_edge_pct"), b(f"✖️ Cost ×{c.get('edge_cost_multiple','2')}", "edge_cost_multiple")],
        [b(f"🛑 Stop {c.get('stop_loss_pct','10')}%", "stop_loss_pct"), b(f"🎯 Take {c.get('take_profit_pct','25')}%", "take_profit_pct")],
        [b(f"🟰 BE trigger {c.get('break_even_trigger_pct','5')}%", "break_even_trigger_pct"), b(f"🔒 BE floor {c.get('break_even_floor_pct','0.25')}%", "break_even_floor_pct")],
        [b(f"📈 Trail trigger {c.get('trailing_trigger_pct','10')}%", "trailing_trigger_pct"), b(f"↘️ Trail gap {c.get('trailing_gap_pct','4')}%", "trailing_gap_pct")],
        [b(f"🚪 Leader-exit cap {c.get('leader_exit_loss_cap_pct','2')}%", "leader_exit_loss_cap_pct"), b(f"💰 Exit floor {c.get('min_exit_profit_pct','0.10')}%", "min_exit_profit_pct")],
        [b(f"📦 Max {c.get('max_positions_per_chain','5')}", "max_positions_per_chain"), b(f"🕒 Hold {c.get('max_hold_hours','24')}h", "max_hold_hours")],
        [b(f"📊 Copied win {c.get('min_copied_win_rate_pct','40')}%", "min_copied_win_rate_pct"), b(f"📈 Copied PF {c.get('min_copied_profit_factor','1')}x", "min_copied_profit_factor")],
        [b(f"🔻 Loss streak {c.get('max_consecutive_copied_losses','3')}", "max_consecutive_copied_losses"), b(f"⏸ Cooldown {c.get('leader_suspend_minutes','360')}m", "leader_suspend_minutes")],
        [b(f"🚨 Daily loss {c.get('daily_loss_limit_pct','4')}%", "daily_loss_limit_pct"), b(f"📉 Portfolio DD {c.get('portfolio_drawdown_limit_pct','12')}%", "portfolio_drawdown_limit_pct")],
        [b(f"↕️ Dyn min {c.get('dynamic_min_allocation_pct','5')}%", "dynamic_min_allocation_pct"), b(f"↕️ Dyn max {c.get('dynamic_max_allocation_pct','20')}%", "dynamic_max_allocation_pct")],
        [b(f"👤 Leader cap {c.get('max_leader_exposure_pct','30')}%", "max_leader_exposure_pct"), b(f"🔒 Profit lock +{c.get('profit_lock_trigger_pct','5')}%", "profit_lock_trigger_pct")],
        [b(f"🔒 Lock size {c.get('profit_lock_size_multiplier_pct','70')}%", "profit_lock_size_multiplier_pct"), b(f"🤝 Consensus +{c.get('leader_consensus_bonus_pct','15')}%", "leader_consensus_bonus_pct")],
        [{"text": f"{'✅' if partial else '❌'} Follow partial sells", "callback_data": "sibot:partial:toggle"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]
    return {"inline_keyboard": rows}


def install():
    _tg.settings_page = settings_page
    _tg.settings_keyboard = settings_keyboard


install()
