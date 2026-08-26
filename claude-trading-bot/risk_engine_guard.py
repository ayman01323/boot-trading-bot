"""Single source of truth for Claude-bot risk definitions.

Consolidated per direct owner instruction (2026-08-26): the previous version
of this module let the operator set MAX_POSITION_USD / MAX_TOTAL_EXPOSURE_USD
/ MAX_DAILY_LOSS_USD / MAX_OPEN_POSITIONS / MAX_DRAWDOWN_PCT as five
independent dollar/percent knobs. The owner's instruction was explicit: do
not hard-code or assume any dollar figure, and do not treat values that
happened to exist in old commits/runtime files as authoritative. Position
size, aggregate exposure, open-position count, and drawdown are now the
owner-approved constants below (OWNER_MAX_*), calculated dynamically against
exactly one operator-provided number: the actual owner-approved Claude
trading capital/equity basis (CLAUDE_CAPITAL_BASIS_USD). There is nothing
left to independently misconfigure -- one basis in, everything else derived.

This module defines position/exposure/open-count limits and the threshold
constants. It does NOT compute drawdown (see claude_state.evaluate_drawdown()
for the equity/high-water-mark model -- an earlier version of this file
computed drawdown itself from closed-position-only realised P&L, which
review (2026-08-26) correctly rejected: it missed unrealised/open-position
losses and mixed a fixed USD basis against a SOL amount priced at read time)
and it does not decide what happens on breach (see claude_state.py for the
persistent HALTED_DRAWDOWN latch) or touch execution (see
solana_execution_risk_patch.py, which consults this module and claude_state
before every buy, after every sell, and on a periodic health check while
ARMED -- see claude_monitor.py). It still sits in front of the reused,
unmodified learnerbot execution engines and never loosens or replaces their
own gates (PoolCheck/RugCheck, slippage, price-impact, liquidity -- see
solana_pool_risk_gate.py / solana_live_executor.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Owner-approved constants (2026-08-26 combined owner instruction). These are
# fixed by direct instruction, not environment-configurable -- there is
# exactly one number left for the operator to supply: the capital basis.
OWNER_MAX_OPEN_POSITIONS = 10
OWNER_MAX_POSITION_PCT = Decimal("3.00")
OWNER_MAX_TOTAL_EXPOSURE_PCT = Decimal("30.00")
OWNER_MAX_DRAWDOWN_PCT = Decimal("20.00")

CAPITAL_BASIS_VAR = "CLAUDE_CAPITAL_BASIS_USD"


class RiskGuardConfigError(RuntimeError):
    """Raised when the hard risk engine config is missing or invalid. Fail closed."""


class DrawdownLimitBreached(RiskGuardConfigError):
    """Raised when current drawdown has reached/exceeded OWNER_MAX_DRAWDOWN_PCT."""

    def __init__(self, *, drawdown_pct: Decimal, drawdown_usd: Decimal):
        self.drawdown_pct = drawdown_pct
        self.drawdown_usd = drawdown_usd
        super().__init__(
            f"Drawdown {drawdown_pct:.2f}% of {CAPITAL_BASIS_VAR} reached/exceeded "
            f"the owner-approved limit {OWNER_MAX_DRAWDOWN_PCT:.2f}%"
        )


def quantize_pct(value: Decimal) -> Decimal:
    """Round to 2dp the same way for every percentage comparison in this
    codebase -- a boundary value like exactly 20.00% must compare identically
    everywhere, not drift between callers that round differently. Public
    (not _-prefixed): claude_state.py's equity/HWM/drawdown model reuses this
    exact rounding rather than defining its own."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RiskLimits:
    capital_basis_usd: Decimal

    @classmethod
    def load(cls) -> "RiskLimits":
        raw = os.environ.get(CAPITAL_BASIS_VAR, "").strip()
        if not raw:
            raise RiskGuardConfigError(
                f"{CAPITAL_BASIS_VAR} is not set, refusing to arm. This must be the "
                f"actual owner-approved Claude trading capital/equity basis -- there "
                f"is no safe default."
            )
        try:
            basis = Decimal(raw)
        except Exception as exc:  # noqa: BLE001
            raise RiskGuardConfigError(f"{CAPITAL_BASIS_VAR}={raw!r} is not a valid number") from exc
        if basis <= 0:
            raise RiskGuardConfigError(f"{CAPITAL_BASIS_VAR} must be > 0, got {basis}")
        return cls(capital_basis_usd=basis)

    @property
    def max_open_positions(self) -> int:
        return OWNER_MAX_OPEN_POSITIONS

    @property
    def max_position_pct(self) -> Decimal:
        return OWNER_MAX_POSITION_PCT

    @property
    def max_total_exposure_pct(self) -> Decimal:
        return OWNER_MAX_TOTAL_EXPOSURE_PCT

    @property
    def max_drawdown_pct(self) -> Decimal:
        return OWNER_MAX_DRAWDOWN_PCT

    @property
    def max_position_usd(self) -> Decimal:
        return self.capital_basis_usd * OWNER_MAX_POSITION_PCT / Decimal(100)

    @property
    def max_total_exposure_usd(self) -> Decimal:
        return self.capital_basis_usd * OWNER_MAX_TOTAL_EXPOSURE_PCT / Decimal(100)

    def position_pct(self, position_usd: Decimal) -> Decimal:
        if self.capital_basis_usd == 0:
            return Decimal("0.00")
        return quantize_pct(position_usd / self.capital_basis_usd * Decimal(100))

    def check_new_position(
        self, *, proposed_usd: Decimal, current_exposure_usd: Decimal, open_positions: int
    ) -> None:
        """Raise RiskGuardConfigError if a proposed position would breach a hard
        limit. Additive pre-check; callers still pass through the reused
        learnerbot pool/token safety gates and execution engines unchanged."""
        if open_positions >= self.max_open_positions:
            raise RiskGuardConfigError(f"Already at the owner-approved maximum of {self.max_open_positions} open positions")
        proposed_pct = self.position_pct(proposed_usd)
        if proposed_pct > OWNER_MAX_POSITION_PCT:
            raise RiskGuardConfigError(
                f"Proposed position ${proposed_usd:.2f} is {proposed_pct:.2f}% of the "
                f"${self.capital_basis_usd:.2f} capital basis, exceeding the owner-approved "
                f"{OWNER_MAX_POSITION_PCT:.2f}% per-position limit (${self.max_position_usd:.2f})"
            )
        total_pct = self.position_pct(current_exposure_usd + proposed_usd)
        if total_pct > OWNER_MAX_TOTAL_EXPOSURE_PCT:
            raise RiskGuardConfigError(
                f"Proposed position would push aggregate exposure to {total_pct:.2f}% of "
                f"the capital basis, exceeding the owner-approved {OWNER_MAX_TOTAL_EXPOSURE_PCT:.2f}% "
                f"limit (${self.max_total_exposure_usd:.2f})"
            )

    # Drawdown is NOT computed here. Per review (2026-08-26), a
    # closed-position-only, capital-basis-relative drawdown missed
    # unrealised (open-position) losses entirely and mixed a fixed USD
    # basis against a SOL-priced-at-read-time swing. The authoritative
    # drawdown calculation is now claude_state.evaluate_drawdown(): a
    # current-trading-equity-vs-persisted-high-water-mark model, fed by
    # solana_execution_risk_patch.compute_current_equity_usd() (capital
    # basis + a running realised-P&L-in-USD total priced once at each
    # trade's own close time, never re-derived from today's price, plus
    # today's mark-to-market of open positions). This class still owns
    # max_drawdown_pct as the threshold constant; DrawdownLimitBreached
    # below is still the exception type raised on breach.
