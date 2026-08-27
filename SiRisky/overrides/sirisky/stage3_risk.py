from __future__ import annotations

import os
from .csvio import read_rows
from .models import RiskDecision
from .wallet import WalletStore
from .jupiter import wallet_balance_lamports


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "pass", "passed"}


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


class Stage3Risk:
    """Stage 3 is the sole BUY risk gate for SiRisky.

    HARD findings block entry. ADVISORY findings are recorded in ``reasons``
    with an ``ADVISORY:`` prefix but do not fail the decision. This keeps the
    HR-CWH routing invariant while allowing explicitly-evidenced high-risk
    pools to be evaluated without weakening sellability/catastrophic controls.
    """

    def __init__(self, settings):
        self.settings = settings

    def _open_count(self):
        rows = read_rows(self.settings.csv_dir / "open_positions.csv")
        return sum(1 for r in rows if str(r.get("status") or "OPEN").upper() == "OPEN")

    @staticmethod
    def _flag(meta: dict, *needles: str) -> bool:
        text = " ".join(
            str(meta.get(k) or "")
            for k in ("risk_flags", "rugcheck_risks", "poolcheck_risks", "risk_text")
        ).lower()
        return any(n.lower() in text for n in needles)

    def check(self, opportunity):
        cfg = self.settings.risk()
        hard: list[str] = []
        advisory: list[str] = []
        snap = opportunity.snapshot
        meta = dict(getattr(snap, "meta", {}) or {})

        # Existing non-negotiable economic/capital gates.
        if opportunity.temperature == "HOT":
            hard.append("HOT_NO_ENTRY")

        min_exit = _num(cfg.get("min_exit_health_pct"), 85.0)
        if snap.exit_health_pct < min_exit:
            hard.append("EXIT_HEALTH")

        min_forecast = _num(cfg.get("min_forecast_net_pct"), 0.25)
        if opportunity.forecast_net_pct < min_forecast:
            hard.append("FORECAST_NET")

        max_round = _num(cfg.get("max_round_trip_cost_pct"), 8.0)
        if snap.round_trip_cost_pct > max_round:
            hard.append("ROUND_TRIP_COST")

        max_open = min(
            int(_num(cfg.get("max_open_positions"), 1)),
            int(_num(os.getenv("MAX_OPEN_POSITIONS", "1"), 1)),
        )
        if self._open_count() >= max_open:
            hard.append("MAX_OPEN_POSITIONS")
        if opportunity.position_sol <= 0:
            hard.append("POSITION_SIZE")

        try:
            address = WalletStore(self.settings).address()
            balance = wallet_balance_lamports(self.settings, address) / 1e9
            reserve = _num(cfg.get("untouched_sol_reserve"), 0.005)
            if balance < opportunity.position_sol + reserve:
                hard.append("INSUFFICIENT_SOL")
        except Exception:
            hard.append("WALLET_BALANCE_UNAVAILABLE")

        # HR-CWH fail-closed controls. Stage 1's successful round-trip quote is
        # itself evidence of sellability; explicit negative telemetry always wins.
        if meta.get("reverse_quote_present") is False or _truthy(meta.get("no_sell")):
            hard.append("NO_EXECUTABLE_REVERSE_QUOTE")
        if _truthy(meta.get("active_liquidity_removal")):
            hard.append("ACTIVE_LIQUIDITY_REMOVAL")
        if _truthy(meta.get("catastrophic_price_impact")):
            hard.append("CATASTROPHIC_PRICE_IMPACT")
        if _truthy(meta.get("failed_simulation")):
            hard.append("FAILED_SIMULATION")
        if _truthy(meta.get("stale_quote")):
            hard.append("STALE_QUOTE")
        if _truthy(meta.get("malicious_deployer")):
            hard.append("MALICIOUS_DEPLOYER")
        if _truthy(meta.get("wallet_signer_overlap")):
            hard.append("WALLET_SIGNER_OWNERSHIP_RISK")

        # Dedicated high-risk policy for the RugCheck "Large Amount of LP
        # Unlocked" condition. It is ADVISORY only when every independent
        # evidence field below is explicitly present and passes. Missing
        # evidence therefore fails closed rather than silently relaxing risk.
        lp_unlocked = (
            _truthy(meta.get("lp_concentration_risk"))
            or self._flag(meta, "large amount of lp unlocked", "lp_concentration_risk")
        )
        if lp_unlocked:
            recent_sell_age = _num(meta.get("recent_sell_sim_age_sec"), 10**9)
            max_sell_age = _num(cfg.get("lp_recent_sell_sim_max_age_sec"), 300.0)
            conditional_pass = all(
                [
                    _truthy(meta.get("lp_depth_test_pass")),
                    _num(meta.get("lp_depth_test_slippage_pct"), 999.0) < 2.0,
                    recent_sell_age <= max_sell_age,
                    _truthy(meta.get("lp_unlock_transparent")),
                    _truthy(meta.get("no_recent_liquidity_withdrawal")),
                    meta.get("reverse_quote_present") is not False,
                    not _truthy(meta.get("active_liquidity_removal")),
                    not _truthy(meta.get("malicious_deployer")),
                    not _truthy(meta.get("stale_quote")),
                ]
            )
            if conditional_pass:
                advisory.append("LP_CONCENTRATION_RISK:Large Amount of LP Unlocked")
            else:
                hard.append("LP_CONCENTRATION_RISK:Large Amount of LP Unlocked")

        if self._flag(meta, "high holder concentration", "single holder ownership"):
            advisory.append("HOLDER_CONCENTRATION")
        if self._flag(meta, "high volatility", "wide spread", "low on-chain history"):
            advisory.append("SOFT_HIGH_RISK_SIGNAL")

        reasons = hard + ["ADVISORY:" + item for item in advisory]
        return RiskDecision(passed=not hard, reasons=reasons, opportunity=opportunity)
