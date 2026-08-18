from __future__ import annotations

import html

from . import solana_sibot as _sol
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_solana_live_patch as _liveui

_PREV_PAGE = _liveui.solana_page


def solana_page(app, tid):
    base = _PREV_PAGE(app, tid)
    cfg = _sol.settings(app)
    lines = [
        "",
        "<b>🎯 SOLANA LEADER QUALITY GATES</b>",
        f"Closed trades <b>{html.escape(str(cfg.get('min_closed_trades','10')))}+</b>  •  win <b>{html.escape(str(cfg.get('min_win_rate_pct','55')))}%+</b>",
        f"Profit factor <b>{html.escape(str(cfg.get('min_profit_factor','1.5')))}x+</b>  •  max drawdown <b>{html.escape(str(cfg.get('max_leader_drawdown_pct','20')))}%</b>",
        f"Recent {html.escape(str(cfg.get('recent_trade_window','20')))}: win <b>{html.escape(str(cfg.get('min_recent_win_rate_pct','55')))}%+</b>  •  PF <b>{html.escape(str(cfg.get('min_recent_profit_factor','1.10')))}x+</b>",
        f"Complete history: <b>{'REQUIRED' if _sol._bool(cfg.get('require_complete_history'),True) else 'optional'}</b>",
        "",
        "<b>🎯 SOLANA ENTRY / EXIT QUALITY</b>",
        f"Signal age <b>≤ {html.escape(str(cfg.get('max_signal_age_seconds','20')))}s</b>  •  worse entry <b>≤ {html.escape(str(cfg.get('max_entry_deterioration_pct','1.5')))}%</b>",
        f"Immediate round trip <b>≤ {html.escape(str(cfg.get('max_roundtrip_loss_pct','2')))}%</b>",
        f"Stop <b>{html.escape(str(cfg.get('stop_loss_pct','10')))}%</b>  •  take <b>{html.escape(str(cfg.get('take_profit_pct','25')))}%</b>",
        f"Break-even +{html.escape(str(cfg.get('break_even_trigger_pct','5')))}% → floor +{html.escape(str(cfg.get('break_even_floor_pct','0.25')))}%",
        f"Trailing +{html.escape(str(cfg.get('trailing_trigger_pct','10')))}% / gap {html.escape(str(cfg.get('trailing_gap_pct','4')))}%  •  leader-exit loss cap {html.escape(str(cfg.get('leader_exit_loss_cap_pct','2')))}%",
        "",
        f"Actual LIVE-copy guard after <b>{html.escape(str(cfg.get('min_copied_trades_for_guard','3')))}</b> closes: win ≥ <b>{html.escape(str(cfg.get('min_copied_win_rate_pct','40')))}%</b>, PF ≥ <b>{html.escape(str(cfg.get('min_copied_profit_factor','1.0')))}x</b>",
        f"After <b>{html.escape(str(cfg.get('max_consecutive_copied_losses','3')))}</b> consecutive copied losses: leader cooldown <b>{html.escape(str(cfg.get('leader_suspend_minutes','360')))} min</b>",
    ]
    return base.rstrip() + "\n" + "\n".join(lines)


def install():
    _liveui.solana_page = solana_page
    _intel.solana_page = solana_page


install()
