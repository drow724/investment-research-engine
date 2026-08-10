from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.domain.portfolio import PortfolioPurpose


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    start: datetime
    end: datetime
    initial_capital: Decimal = Decimal("100000")
    rebalance_days: int = 7
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0.001")
    portfolio_id: str = "crypto-paper"
    purpose: PortfolioPurpose = PortfolioPurpose.PAPER_TRADING

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("backtest timestamps must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("backtest start must precede end")
        if self.initial_capital <= 0 or self.rebalance_days <= 0:
            raise ValueError("capital and rebalance_days must be positive")
        if min(self.fee_rate, self.slippage_rate) < 0:
            raise ValueError("cost rates cannot be negative")
        if self.purpose is PortfolioPurpose.CORE_INVESTMENT:
            raise ValueError("core investment capital cannot be backtested by crypto trading")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    gross_equity: Decimal
    cash_weight: Decimal


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
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


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_name: str
    strategy_version: str
    portfolio_id: str
    purpose: PortfolioPurpose
    start: datetime
    end: datetime
    metrics: PerformanceMetrics
    equity_curve: tuple[EquityPoint, ...]
    rebalance_count: int
    rejected_rebalances: int
