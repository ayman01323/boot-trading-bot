from __future__ import annotations

import time
from .jupiter import quote_only, WSOL_MINT, token_balance_raw
from .wallet import WalletStore


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


class Stage6Monitor:
    """Fast HR-CWH monitor for already-open positions only.

    All P&L decisions use an executable reverse Jupiter quote. Stage 6 never
    closes state directly; it only emits HOLD/EXIT back to Stage 4 via engine.py.
    """

    def __init__(self, settings):
        self.settings = settings
        self._peak_net_pct: dict[str, float] = {}

    def evaluate(self, position: dict):
        wallet = WalletStore(self.settings)
        address = wallet.address()
        mint = str(position["mint"])
        token_raw = int(position.get("token_raw") or 0)

        if str(position.get("mode") or "").upper() == "LIVE":
            actual = token_balance_raw(self.settings, address, mint)
            if actual > 0:
                token_raw = actual
        if token_raw <= 0:
            return {
                "decision": "EXIT",
                "reason": "NO_TOKEN_BALANCE",
                "sell_raw": 0,
                "net_pct": -100.0,
                "exit_health_pct": 0.0,
                "temperature": "HOT",
            }

        # A fresh exact-position reverse quote is mandatory for every monitor
        # decision; failure raises and leaves the authoritative position OPEN.
        q = quote_only(self.settings, address, mint, WSOL_MINT, token_raw)
        sell_lamports = int(q["out_amount"] or 0)
        if sell_lamports <= 0:
            raise RuntimeError("REVERSE_QUOTE_UNAVAILABLE")

        entry = max(1, int(position.get("entry_lamports") or 1))
        net_pct = (sell_lamports - entry) / entry * 100.0
        exit_health_pct = (sell_lamports / entry) * 100.0
        cfg = self.settings.risk()

        configured_target = _num(position.get("target_net_pct"), 3.0)
        tp_floor = _num(cfg.get("fast_take_profit_floor_pct"), 2.0)
        tp_cap = max(tp_floor, _num(cfg.get("fast_take_profit_cap_pct"), 5.0))
        target = min(tp_cap, max(tp_floor, configured_target))

        configured_hold = int(_num(position.get("max_hold_seconds"), 90))
        max_hold_cap = max(30, int(_num(cfg.get("fast_max_hold_cap_seconds"), 300)))
        max_hold = min(max(1, configured_hold), max_hold_cap)
        age = int(time.time()) - int(_num(position.get("opened_epoch"), time.time()))

        position_id = str(position.get("position_id") or mint)
        previous_peak = self._peak_net_pct.get(position_id, net_pct)
        peak_net = max(previous_peak, net_pct)
        self._peak_net_pct[position_id] = peak_net
        reversal_from_peak = max(0.0, peak_net - net_pct)

        warm_reversal = max(0.1, _num(cfg.get("warm_reversal_pct"), 1.5))
        hot_reversal = max(warm_reversal, _num(cfg.get("hot_reversal_pct"), 3.0))
        fast_stop = -abs(_num(cfg.get("fast_stop_net_pct"), 3.0))
        min_health = _num(cfg.get("min_exit_health_pct"), 85.0)

        base = {
            "sell_raw": token_raw,
            "net_pct": net_pct,
            "sell_lamports": sell_lamports,
            "exit_health_pct": exit_health_pct,
            "peak_net_pct": peak_net,
            "reversal_from_peak_pct": reversal_from_peak,
            "time_in_trade_sec": age,
            "dynamic_target_net_pct": target,
        }

        # Fast profit extraction: the configured executable-net target is
        # constrained to the owner's requested 2-5% band.
        if net_pct >= target:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "FAST_TAKE_PROFIT", "temperature": "WARM"}

        # HOT structural/executable deterioration exits before ordinary timing.
        if exit_health_pct < min_health:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "EXIT_HEALTH", "temperature": "HOT"}
        if net_pct <= fast_stop:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "FAST_STOP", "temperature": "HOT"}
        if reversal_from_peak >= hot_reversal:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "HOT_REVERSAL", "temperature": "HOT"}

        # WARM reversal is deliberately an exit for this short-horizon profile:
        # preserve a small move rather than waiting for a larger drawdown.
        if peak_net > 0 and reversal_from_peak >= warm_reversal:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "WARM_REVERSAL", "temperature": "WARM"}

        if age >= max_hold:
            self._peak_net_pct.pop(position_id, None)
            return {**base, "decision": "EXIT", "reason": "MAX_HOLD_TIME", "temperature": "HOT"}

        temperature = "WARM" if reversal_from_peak > 0 or net_pct < 0 else "COLD"
        return {**base, "decision": "HOLD", "reason": "FAST_TARGET_NOT_MET", "temperature": temperature}
