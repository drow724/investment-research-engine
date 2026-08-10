from typing import Protocol

from investment.crypto.domain.portfolio import PortfolioAllocation, TradingPortfolio


class AllocationRiskDecision(Protocol):
    @property
    def approved(self) -> bool: ...


class AllocationRiskEngine(Protocol):
    def evaluate_allocation(
        self, allocation: PortfolioAllocation, portfolio: TradingPortfolio
    ) -> AllocationRiskDecision:
        ...
