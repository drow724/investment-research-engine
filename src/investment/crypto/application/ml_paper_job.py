"""Safe ML paper-allocation job; execution is intentionally out of scope."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.application.ml_service import (
    CryptoMLService,
    PredictReturnsCommand,
)
from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.domain.market import MarketRegime
from investment.crypto.domain.portfolio import Position, TradingPortfolio
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy
from investment.crypto.strategy.ml_ranking import MLRankingStrategy, RankedReturn


@dataclass(frozen=True, slots=True)
class RunMLPaperJobCommand:
    portfolio_id: str
    pair_symbols: tuple[str, ...]
    as_of: datetime
    model_id: str | None = None
    regime: MarketRegime = MarketRegime.NEUTRAL
    maximum_positions: int = 3
    minimum_expected_return: float = 0.0
    dry_run: bool = True


@dataclass(frozen=True, slots=True)
class TargetWeight:
    asset: str
    weight: Decimal


@dataclass(frozen=True, slots=True)
class RunMLPaperJobResult:
    model_id: str
    portfolio_id: str
    as_of: datetime
    dry_run: bool
    risk_approved: bool
    risk_violations: tuple[str, ...]
    targets: tuple[TargetWeight, ...]
    cash_weight: Decimal
    execution_status: str


class MLPaperJobService:
    def __init__(self, ml: CryptoMLService, paper: PaperTradingService) -> None:
        self._ml = ml
        self._paper = paper

    def run(self, command: RunMLPaperJobCommand) -> RunMLPaperJobResult:
        if not command.dry_run:
            raise ValueError(
                "ML job execution is disabled; use dryRun=true until exchange rules "
                "and reconciliation exist"
            )
        snapshot = self._paper.portfolio(command.portfolio_id)
        portfolio = TradingPortfolio(
            snapshot.portfolio_id,
            snapshot.purpose,
            snapshot.cash_asset,
            snapshot.cash_balance,
            tuple(
                Position(item.asset, item.quantity, item.average_cost)
                for item in snapshot.positions
            ),
        )
        prediction = self._ml.predict(
            PredictReturnsCommand(command.pair_symbols, command.as_of, command.model_id)
        )
        strategy = MLRankingStrategy(
            command.maximum_positions, command.minimum_expected_return
        ).generate(
            tuple(
                RankedReturn(item.asset, item.expected_return) for item in prediction.predictions
            ),
            model_id=prediction.model_id,
            as_of=prediction.as_of,
            regime=command.regime,
        )
        allocation = EqualWeightPortfolioConstructor(
            maximum_positions=command.maximum_positions,
            maximum_asset_weight=Decimal("0.5"),
        ).construct(portfolio, strategy)
        decision = DeterministicRiskEngine(
            RiskPolicy(maximum_positions=command.maximum_positions)
        ).evaluate_allocation(allocation, portfolio)
        return RunMLPaperJobResult(
            prediction.model_id,
            portfolio.portfolio_id,
            command.as_of,
            True,
            decision.approved,
            decision.violations,
            tuple(TargetWeight(item.asset.symbol, item.weight) for item in allocation.allocations),
            allocation.cash_weight,
            "DRY_RUN_ONLY",
        )
