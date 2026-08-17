"""Paper execution orchestration; strategies cannot call this service directly."""

from decimal import Decimal

from investment.crypto.domain.accounting import (
    PaperExecutionRecord,
    PaperPortfolioSnapshot,
    PaperRebalanceDecisionRecord,
)
from investment.crypto.domain.market import Asset, AssetKind
from investment.crypto.domain.order import ApprovedOrder
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.ports.accounting import PaperPortfolioRepository
from investment.crypto.ports.exchange import ExchangeGateway, ExecutionReport


class PaperTradingService:
    def __init__(self, gateway: ExchangeGateway, repository: PaperPortfolioRepository) -> None:
        self._gateway = gateway
        self._repository = repository

    def execute(self, order: ApprovedOrder) -> tuple[ExecutionReport, bool]:
        report = self._gateway.submit(order)
        applied = self._repository.apply_execution(order, report)
        return report, applied

    def portfolio(self, portfolio_id: str) -> PaperPortfolioSnapshot:
        return self._repository.get(portfolio_id)

    def executions(self, portfolio_id: str, limit: int = 100) -> tuple[PaperExecutionRecord, ...]:
        return self._repository.list_executions(portfolio_id, limit)

    def rebalance_decisions(
        self, portfolio_id: str, limit: int = 100
    ) -> tuple[PaperRebalanceDecisionRecord, ...]:
        return self._repository.list_rebalance_decisions(portfolio_id, limit)

    def create_portfolio(
        self,
        portfolio_id: str,
        purpose: PortfolioPurpose,
        cash_symbol: str,
        initial_cash: Decimal,
    ) -> PaperPortfolioSnapshot:
        portfolio = TradingPortfolio(
            portfolio_id,
            purpose,
            Asset(cash_symbol, AssetKind.CASH),
            initial_cash,
        )
        return self._repository.create(portfolio)
