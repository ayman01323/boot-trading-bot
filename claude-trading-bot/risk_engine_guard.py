"""Additional hard risk limits for claude-trading-bot.

This sits IN FRONT OF the reused learnerbot execution engines. It only ever adds
a tighter constraint on top of the existing, unmodified gates in
learnerbot/live_executor.py, learnerbot/solana_live_executor.py,
evm_pool_rug_gate.py and solana_pool_risk_gate.py — it never loosens or replaces
any of them. If a required limit is missing or invalid, load() raises and the
caller (run.py) must refuse to start LIVE mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

REQUIRED_FLOAT_VARS = (
    "MAX_CAPITAL_USD",
    "MAX_POSITION_USD",
    "MAX_TOTAL_EXPOSURE_USD",
    "MAX_DAILY_LOSS_USD",
    "MAX_DRAWDOWN_PCT",
    "MAX_SLIPPAGE_PCT",
    "MAX_PRICE_IMPACT_PCT",
    "MIN_POOL_LIQUIDITY_USD",
)
REQUIRED_INT_VARS = ("MAX_OPEN_POSITIONS",)


class RiskGuardConfigError(RuntimeError):
    """Raised when the hard risk engine config is missing or invalid. Fail closed."""


@dataclass(frozen=True)
class RiskLimits:
    max_capital_usd: float
    max_position_usd: float
    max_total_exposure_usd: float
    max_open_positions: int
    max_daily_loss_usd: float
    max_drawdown_pct: float
    max_slippage_pct: float
    max_price_impact_pct: float
    min_pool_liquidity_usd: float

    @classmethod
    def load(cls) -> "RiskLimits":
        missing = [v for v in REQUIRED_FLOAT_VARS + REQUIRED_INT_VARS if not os.environ.get(v, "").strip()]
        if missing:
            raise RiskGuardConfigError(
                "Missing required hard risk engine variables, refusing to arm: " + ", ".join(missing)
            )

        values: dict[str, float] = {}
        for name in REQUIRED_FLOAT_VARS:
            raw = os.environ[name].strip()
            try:
                val = float(raw)
            except ValueError as exc:
                raise RiskGuardConfigError(f"{name}={raw!r} is not a valid number") from exc
            if val <= 0:
                raise RiskGuardConfigError(f"{name} must be > 0, got {val}")
            values[name] = val

        raw_positions = os.environ["MAX_OPEN_POSITIONS"].strip()
        try:
            max_open_positions = int(raw_positions)
        except ValueError as exc:
            raise RiskGuardConfigError(f"MAX_OPEN_POSITIONS={raw_positions!r} is not a valid integer") from exc
        if max_open_positions <= 0:
            raise RiskGuardConfigError(f"MAX_OPEN_POSITIONS must be > 0, got {max_open_positions}")

        if values["MAX_POSITION_USD"] > values["MAX_CAPITAL_USD"]:
            raise RiskGuardConfigError("MAX_POSITION_USD cannot exceed MAX_CAPITAL_USD")
        if values["MAX_TOTAL_EXPOSURE_USD"] > values["MAX_CAPITAL_USD"]:
            raise RiskGuardConfigError("MAX_TOTAL_EXPOSURE_USD cannot exceed MAX_CAPITAL_USD")
        if not (0 < values["MAX_DRAWDOWN_PCT"] <= 100):
            raise RiskGuardConfigError("MAX_DRAWDOWN_PCT must be within (0, 100]")
        if not (0 < values["MAX_SLIPPAGE_PCT"] <= 100):
            raise RiskGuardConfigError("MAX_SLIPPAGE_PCT must be within (0, 100]")
        if not (0 < values["MAX_PRICE_IMPACT_PCT"] <= 100):
            raise RiskGuardConfigError("MAX_PRICE_IMPACT_PCT must be within (0, 100]")

        return cls(
            max_capital_usd=values["MAX_CAPITAL_USD"],
            max_position_usd=values["MAX_POSITION_USD"],
            max_total_exposure_usd=values["MAX_TOTAL_EXPOSURE_USD"],
            max_open_positions=max_open_positions,
            max_daily_loss_usd=values["MAX_DAILY_LOSS_USD"],
            max_drawdown_pct=values["MAX_DRAWDOWN_PCT"],
            max_slippage_pct=values["MAX_SLIPPAGE_PCT"],
            max_price_impact_pct=values["MAX_PRICE_IMPACT_PCT"],
            min_pool_liquidity_usd=values["MIN_POOL_LIQUIDITY_USD"],
        )

    def check_new_position(self, *, proposed_usd: float, current_exposure_usd: float, open_positions: int) -> None:
        """Raise RiskGuardConfigError if a proposed position would breach a hard limit.

        This is an additive pre-check; callers must still pass through the reused
        learnerbot pool/token safety gates and execution engines unchanged.
        """
        if proposed_usd > self.max_position_usd:
            raise RiskGuardConfigError(
                f"Proposed position ${proposed_usd:.2f} exceeds MAX_POSITION_USD ${self.max_position_usd:.2f}"
            )
        if current_exposure_usd + proposed_usd > self.max_total_exposure_usd:
            raise RiskGuardConfigError(
                f"Proposed position would push exposure to "
                f"${current_exposure_usd + proposed_usd:.2f}, exceeding "
                f"MAX_TOTAL_EXPOSURE_USD ${self.max_total_exposure_usd:.2f}"
            )
        if open_positions >= self.max_open_positions:
            raise RiskGuardConfigError(
                f"Already at MAX_OPEN_POSITIONS ({self.max_open_positions})"
            )
