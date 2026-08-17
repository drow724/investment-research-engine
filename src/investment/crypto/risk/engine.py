from dataclasses import dataclass
from decimal import Decimal

from investment.crypto.domain.order import ApprovedOrder, OrderIntent
from investment.crypto.domain.portfolio import (
    PortfolioAllocation,
    PortfolioPurpose,
    TradingPortfolio,
)


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    version: str = "v1"
    maximum_invested_fraction: Decimal = Decimal("1")
    maximum_asset_fraction: Decimal = Decimal("0.5")
    maximum_positions: int = 3
    maximum_single_order_notional: Decimal = Decimal("100000")
    minimum_order_notional: Decimal = Decimal("5000")
    kill_switch: bool = False

    def __post_init__(self) -> None:
        if self.maximum_positions <= 0:
            raise ValueError("maximum_positions must be positive")
        if not Decimal("0") <= self.maximum_invested_fraction <= Decimal("1"):
            raise ValueError("maximum invested fraction must be within [0, 1]")
        if not Decimal("0") <= self.maximum_asset_fraction <= Decimal("1"):
            raise ValueError("maximum asset fraction must be within [0, 1]")
        if min(self.maximum_single_order_notional, self.minimum_order_notional) < 0:
            raise ValueError("order notional limits cannot be negative")
        if self.minimum_order_notional > self.maximum_single_order_notional:
            raise ValueError("minimum order notional cannot exceed maximum")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    policy_version: str
    violations: tuple[str, ...]


class DeterministicRiskEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy

    def evaluate_allocation(
        self, allocation: PortfolioAllocation, portfolio: TradingPortfolio
    ) -> RiskDecision:
        violations: list[str] = []
        if self.policy.kill_switch:
            violations.append("KILL_SWITCH_ACTIVE")
        if allocation.portfolio_id != portfolio.portfolio_id:
            violations.append("PORTFOLIO_ID_MISMATCH")
        if allocation.purpose != portfolio.purpose:
            violations.append("PORTFOLIO_PURPOSE_MISMATCH")
        if portfolio.purpose is PortfolioPurpose.CORE_INVESTMENT:
            violations.append("CORE_INVESTMENT_FORBIDDEN")
        if len(allocation.allocations) > self.policy.maximum_positions:
            violations.append("MAXIMUM_POSITIONS_EXCEEDED")
        invested = Decimal("1") - allocation.cash_weight
        if invested > self.policy.maximum_invested_fraction:
            violations.append("MAXIMUM_PORTFOLIO_ALLOCATION_EXCEEDED")
        if any(item.weight > self.policy.maximum_asset_fraction for item in allocation.allocations):
            violations.append("MAXIMUM_ASSET_ALLOCATION_EXCEEDED")
        return RiskDecision(not violations, self.policy.version, tuple(violations))

    def approve_order(self, intent: OrderIntent, reference_price: Decimal) -> ApprovedOrder:
        if self.policy.kill_switch:
            raise ValueError("kill switch is active")
        if intent.purpose is PortfolioPurpose.CORE_INVESTMENT:
            raise ValueError("core investment orders are forbidden")
        notional = intent.quantity * reference_price
        if notional < self.policy.minimum_order_notional:
            raise ValueError("order is below the minimum notional")
        if notional > self.policy.maximum_single_order_notional:
            raise ValueError("order exceeds the maximum notional")
        return ApprovedOrder(intent, self.policy.version)
