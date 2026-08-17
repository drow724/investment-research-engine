from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class CryptoApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class MomentumBacktestRequest(CryptoApiModel):
    pairs: tuple[str, ...] = Field(min_length=1)
    start: datetime
    end: datetime
    initial_capital: Decimal = Field(default=Decimal("100000"), gt=0)
    portfolio_purpose: Literal["PAPER_TRADING", "SYSTEMATIC_TRADING"] = "PAPER_TRADING"
    lookback_days: int = Field(default=30, ge=2, le=365)
    maximum_positions: int = Field(default=3, ge=1, le=10)
    rebalance_days: int = Field(default=7, ge=1, le=90)
    minimum_average_quote_volume: Decimal = Field(default=Decimal("0"), ge=0)
    fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, le=Decimal("0.1"))
    slippage_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.1"))

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("backtest timestamps must be timezone-aware")
        return value


class PerformanceMetricsResponse(CryptoApiModel):
    total_return: float
    gross_total_return: float
    fee_adjusted_return: float
    slippage_adjusted_return: float
    cagr: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    maximum_drawdown: float
    volatility: float
    hit_rate: float
    turnover: float
    total_fee_impact: float
    total_slippage_impact: float
    buy_and_hold_return: float
    cash_benchmark_return: float


class EquityPointResponse(CryptoApiModel):
    timestamp: datetime
    equity: Decimal
    gross_equity: Decimal
    cash_weight: Decimal


class MomentumBacktestResponse(CryptoApiModel):
    engine: Literal["crypto_trading"] = "crypto_trading"
    operation: Literal["momentum_backtest"] = "momentum_backtest"
    status: Literal["COMPLETED"] = "COMPLETED"
    strategy: str
    strategy_version: str
    portfolio_id: str
    portfolio_purpose: str
    start: datetime
    end: datetime
    metrics: PerformanceMetricsResponse
    equity_curve: tuple[EquityPointResponse, ...]
    rebalance_count: int
    rejected_rebalances: int


class IntradayBacktestRequest(CryptoApiModel):
    pairs: tuple[str, ...] = Field(min_length=2)
    start: datetime
    end: datetime
    initial_capital: Decimal = Field(default=Decimal("1000000"), gt=0)
    signal_lookback_bars: int = Field(default=16, ge=2, le=672)
    rebalance_bars: int = Field(default=4, ge=1, le=96)
    maximum_positions: int = Field(default=3, ge=1, le=10)
    fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, le=Decimal("0.1"))
    slippage_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.1"))

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("intraday timestamps must be timezone-aware")
        return value
