"""Trading portfolio identity, accounting primitives, and allocation proposals."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from investment.crypto.domain.market import Asset


class PortfolioPurpose(StrEnum):
    CORE_INVESTMENT = "CORE_INVESTMENT"
    SYSTEMATIC_TRADING = "SYSTEMATIC_TRADING"
    PAPER_TRADING = "PAPER_TRADING"


@dataclass(frozen=True, slots=True)
class Position:
    asset: Asset
    quantity: Decimal
    average_cost: Decimal

    def __post_init__(self) -> None:
        if self.quantity < 0 or self.average_cost < 0:
            raise ValueError("spot positions cannot have negative quantity or cost")


@dataclass(frozen=True, slots=True)
class TradingPortfolio:
    portfolio_id: str
    purpose: PortfolioPurpose
    cash_asset: Asset
    cash_balance: Decimal
    positions: tuple[Position, ...] = ()

    def __post_init__(self) -> None:
        if self.purpose is PortfolioPurpose.CORE_INVESTMENT:
            raise ValueError("core investment holdings cannot enter the crypto trading engine")
        if self.cash_balance < 0:
            raise ValueError("cash balance cannot be negative")


@dataclass(frozen=True, slots=True)
class AssetAllocation:
    asset: Asset
    weight: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.weight <= Decimal("1"):
            raise ValueError("allocation weight must be between zero and one")


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    portfolio_id: str
    purpose: PortfolioPurpose
    allocations: tuple[AssetAllocation, ...]
    cash_weight: Decimal

    def __post_init__(self) -> None:
        if self.purpose is PortfolioPurpose.CORE_INVESTMENT:
            raise ValueError("core investment allocation is outside this engine")
        if not Decimal("0") <= self.cash_weight <= Decimal("1"):
            raise ValueError("cash weight must be between zero and one")
        assets = [item.asset for item in self.allocations]
        if len(assets) != len(set(assets)):
            raise ValueError("allocation assets must be unique")
        total = sum((item.weight for item in self.allocations), self.cash_weight)
        if total != Decimal("1"):
            raise ValueError("asset and cash weights must sum exactly to one")
