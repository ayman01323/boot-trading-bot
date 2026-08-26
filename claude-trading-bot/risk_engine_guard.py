"""Additional hard risk limits for claude-trading-bot.

This sits IN FRONT OF the reused learnerbot execution engines — actually
consulted by solana_execution_risk_patch.py before every Solana LIVE buy,
not just validated at startup (that gap was flagged in review and fixed).
It only ever adds a tighter constraint on top of the existing, unmodified
gates in learnerbot/live_executor.py, learnerbot/solana_live_executor.py,
evm_pool_rug_gate.py and solana_pool_risk_gate.py — it never loosens or
replaces any of them. If a required limit is missing or invalid, load()
raises and the caller must refuse to start LIVE mode.

Scope note (per review): this contract intentionally does NOT include
slippage, price-impact, or minimum-liquidity limits. Those dimensions are
already governed by the reused, already-reviewed code this bot runs
unmodified:
  - price impact: solana_pool_risk_gate.py's reference_reverse_depth_check(),
    hard-capped at 200 bps
  - minimum liquidity / LP lock: solana_pool_risk_gate.py's
    evaluate_rugcheck() (LP-locked-pct floor) and evaluate_dexscreener()
    (liquidity floor, pool-age cooling, liquidity-collapse detection)
  - slippage: solana_live_executor.py's Jupiter slippageBps parameter plus
    its mandatory post-execution economic validation (rejects if executed
    input/output don't reconcile)
A second, Claude-specific implementation of the same checks would either be
redundant with those or, worse, subtly inconsistent with logic that's
already been reviewed — so this module does not attempt it. What IS unique
to this contract (nothing reused already tracks these in absolute USD terms
for this specific instance) is daily loss and drawdown, which
check_daily_loss_and_drawdown() enforces for real against this instance's
own closed-position history.
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
)
REQUIRED_INT_VARS = ("MAX_OPEN_POSITIONS",)


class RiskGuardConfigError(RuntimeError):
    """Raised when the hard risk engine config is missing or invalid. Fail closed."""


class DrawdownLimitBreached(RiskGuardConfigError):
    """Specific drawdown breach used by the execution layer to latch trading off."""

    def __init__(self, *, drawdown_pct: float, limit_pct: float, drawdown_usd: float):
        self.drawdown_pct = float(drawdown_pct)
        self.limit_pct = float(limit_pct)
        self.drawdown_usd = float(drawdown_usd)
        super().__init__(
            f"Drawdown {self.drawdown_pct:.2f}% of MAX_CAPITAL_USD reached/exceeded "
            f"MAX_DRAWDOWN_PCT {self.limit_pct:.2f}%"
        )


@dataclass(frozen=True)
class RiskLimits:
    max_capital_usd: float
    max_position_usd: float
    max_total_exposure_usd: float
    max_open_positions: int
    max_daily_loss_usd: float
    max_drawdown_pct: float

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

        return cls(
            max_capital_usd=values["MAX_CAPITAL_USD"],
            max_position_usd=values["MAX_POSITION_USD"],
            max_total_exposure_usd=values["MAX_TOTAL_EXPOSURE_USD"],
            max_open_positions=max_open_positions,
            max_daily_loss_usd=values["MAX_DAILY_LOSS_USD"],
            max_drawdown_pct=values["MAX_DRAWDOWN_PCT"],
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

    def check_daily_loss_and_drawdown(
        self, *, realized_pnl_usd_today: float, peak_to_current_drawdown_usd: float
    ) -> None:
        """Raise RiskGuardConfigError if realized daily loss or drawdown breach limits.

        Callers compute realized_pnl_usd_today (sum of realized P&L for
        positions closed since the start of the current UTC day) and
        peak_to_current_drawdown_usd (running peak of cumulative realized
        P&L minus current cumulative realized P&L, i.e. how far below the
        active risk baseline's best point this instance's equity has fallen)
        from this instance's own isolated position history — see
        solana_execution_risk_patch.py.
        """
        if realized_pnl_usd_today < 0 and -realized_pnl_usd_today > self.max_daily_loss_usd:
            raise RiskGuardConfigError(
                f"Realized loss today ${-realized_pnl_usd_today:.2f} exceeds "
                f"MAX_DAILY_LOSS_USD ${self.max_daily_loss_usd:.2f}"
            )
        drawdown_pct = (peak_to_current_drawdown_usd / self.max_capital_usd) * 100 if self.max_capital_usd else 0.0
        if drawdown_pct >= self.max_drawdown_pct:
            raise DrawdownLimitBreached(
                drawdown_pct=drawdown_pct,
                limit_pct=self.max_drawdown_pct,
                drawdown_usd=peak_to_current_drawdown_usd,
            )
