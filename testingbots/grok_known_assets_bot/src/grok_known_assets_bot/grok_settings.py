from pydantic import BaseModel, Field, model_validator, ConfigDict


class GrokResearchSettings(BaseModel):
    """
    Standalone Pydantic settings model for PAPER/SHADOW market-research module.
    Contains only research thresholds, defaults, and validation.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)

    # Research quality thresholds
    min_confidence: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Minimum required model confidence (0.0-1.0)"
    )
    max_source_age_seconds: float = Field(
        default=20.0,
        ge=0.0,
        description="Maximum allowed age of source data in seconds"
    )
    max_spread_bps: float = Field(
        default=80.0,
        ge=0.0,
        description="Maximum acceptable bid-ask spread in basis points"
    )
    max_impact_bps: float = Field(
        default=100.0,
        ge=0.0,
        description="Maximum allowable market impact in basis points"
    )
    min_liquidity_usd: float = Field(
        default=250000.0,
        ge=0.0,
        description="Minimum required on-chain liquidity in USD"
    )
    min_volume_5m_usd: float = Field(
        default=25000.0,
        ge=0.0,
        description="Minimum 5-minute trading volume in USD"
    )

    # Momentum thresholds - expressed as percentage points.
    # Native SOL is allowed to be briefly flat/slightly negative so that the
    # scorer can recognise pullbacks and early trend formation instead of only
    # buying after a +0.30% five-minute move has already happened.
    momentum_5m_min_pct: float = Field(
        default=-0.05,
        description="Hard minimum 5m momentum (-0.05 = -0.05%)"
    )
    momentum_5m_max_pct: float = Field(
        default=5.00,
        description="Maximum 5m momentum in percentage points (5.00 = 5.00%)"
    )
    momentum_1m_min_pct: float = Field(
        default=-0.50,
        description="Minimum 1m momentum in percentage points (-0.50 = -0.50%)"
    )
    momentum_15m_min_pct: float = Field(
        default=-0.30,
        description="Hard minimum 15m momentum (-0.30 = -0.30%)"
    )
    require_positive_momentum_15m: bool = Field(
        default=False,
        description="Legacy strict mode: require 15m momentum to be > 0 instead of using momentum_15m_min_pct"
    )

    # Edge and risk parameters
    min_net_edge_pct: float = Field(
        default=0.60,
        ge=0.0,
        description="Minimum net edge in percentage points (0.60 = 0.60%)"
    )

    # Risk management - expressed as decimal fractions (e.g. 0.025 = 2.5%)
    stop_loss_min_fraction: float = Field(
        default=0.025,
        description="Minimum stop-loss as decimal fraction (0.025 = 2.5%)"
    )
    stop_loss_max_fraction: float = Field(
        default=0.040,
        description="Maximum stop-loss as decimal fraction (0.040 = 4.0%)"
    )
    take_profit_1_fraction: float = Field(
        default=0.020,
        description="First take-profit level as decimal fraction (0.020 = 2.0%)"
    )
    take_profit_2_fraction: float = Field(
        default=0.040,
        description="Second take-profit level as decimal fraction (0.040 = 4.0%)"
    )
    trailing_drawdown_fraction: float = Field(
        default=0.010,
        description="Trailing drawdown threshold as decimal fraction (0.010 = 1.0%)"
    )
    max_hold_minutes: int = Field(
        default=60,
        gt=0,
        description="Maximum position hold time in minutes"
    )

    @model_validator(mode='after')
    def validate_momentum(self) -> 'GrokResearchSettings':
        if self.momentum_5m_min_pct > self.momentum_5m_max_pct:
            raise ValueError("momentum_5m_min_pct must be <= momentum_5m_max_pct")
        return self

    @model_validator(mode='after')
    def validate_stop_loss(self) -> 'GrokResearchSettings':
        if self.stop_loss_min_fraction <= 0:
            raise ValueError("stop_loss_min_fraction must be > 0")
        if self.stop_loss_min_fraction > self.stop_loss_max_fraction:
            raise ValueError("stop_loss_min_fraction must be <= stop_loss_max_fraction")
        return self

    @model_validator(mode='after')
    def validate_profit_and_trailing(self) -> 'GrokResearchSettings':
        for field in (self.take_profit_1_fraction, self.take_profit_2_fraction, self.trailing_drawdown_fraction):
            if field <= 0:
                raise ValueError("take_profit and trailing_drawdown fractions must be > 0")
        return self


# Default instance
settings = GrokResearchSettings()
