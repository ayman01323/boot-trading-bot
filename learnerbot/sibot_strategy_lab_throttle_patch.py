from __future__ import annotations

import time
from decimal import Decimal

from . import sibot as _sibot
from . import strategy_lab as _lab
from . import strategy_lab_live_recording_patch as _recording

# The only automatic action this module takes: proportionally reduce a chain's
# SiBot LEADER_COPY position size when the Strategy Lab's evidence-based evaluation
# (fed real closed-trade data by strategy_lab_live_recording_patch.py) says that
# chain's copy-trading has become REPLACE/REWORK-worthy. This reuses the exact
# mechanism sibot_profit_guard_patch.py already applies for its own daily
# profit-lock throttle (_position_size *= multiplier) -- no new kind of capital
# control, no bypassed execution/safety gate, and never a hard stop (a strategy
# that should stop entirely still requires a human/operator decision).

_PREV_POSITION_SIZE = _sibot._position_size

_EVAL_TTL_SECONDS = 900  # avoid a strategy_lab evaluation on every single trade
_REPLACE_MULTIPLIER = Decimal("0.25")
_REWORK_MULTIPLIER = Decimal("0.60")

_cache: dict[str, tuple[int, Decimal, str]] = {}


def _strategy_lab_multiplier(app, chain_slug: str) -> tuple[Decimal, str]:
    now = int(time.time())
    cached = _cache.get(chain_slug)
    if cached and now - cached[0] < _EVAL_TTL_SECONDS:
        return cached[1], cached[2]
    multiplier = Decimal(1)
    status = "UNKNOWN"
    try:
        sid = _recording.leader_copy_strategy_id(app, chain_slug)
        decision = _lab.evaluate_strategy(app, sid, mode="LIVE")
        status = str(decision.get("status") or "")
        if status == "REPLACE":
            multiplier = _REPLACE_MULTIPLIER
        elif status == "REWORK":
            multiplier = _REWORK_MULTIPLIER
    except Exception:
        multiplier = Decimal(1)
        status = "ERROR"
    _cache[chain_slug] = (now, multiplier, status)
    return multiplier, status


def _position_size_with_strategy_lab(app, telegram_id, trader, cfg) -> Decimal:
    amount = _PREV_POSITION_SIZE(app, telegram_id, trader, cfg)
    if amount <= 0:
        return amount
    multiplier, _status = _strategy_lab_multiplier(app, trader.chain.slug)
    if multiplier >= 1:
        return amount
    reduced = amount * multiplier
    try:
        minimum = Decimal(str(cfg.get("min_trade_native") or "0.0001"))
    except Exception:
        minimum = Decimal("0.0001")
    return reduced if reduced >= minimum else Decimal(0)


def install():
    if getattr(_sibot, "_strategy_lab_throttle_installed", False):
        return
    _sibot._position_size = _position_size_with_strategy_lab
    _sibot._strategy_lab_throttle_installed = True


install()
