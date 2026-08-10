from typing import Protocol

from investment.crypto.domain.accounting import PaperPortfolioSnapshot
from investment.crypto.domain.order import ApprovedOrder
from investment.crypto.domain.portfolio import TradingPortfolio
from investment.crypto.ports.exchange import ExecutionReport


class PaperPortfolioRepository(Protocol):
    def create(self, portfolio: TradingPortfolio) -> PaperPortfolioSnapshot: ...

    def apply_execution(
        self, order: ApprovedOrder, report: ExecutionReport
    ) -> bool: ...

    def get(self, portfolio_id: str) -> PaperPortfolioSnapshot: ...
