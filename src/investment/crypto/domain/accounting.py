"""Persistent paper-accounting read models."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.domain.market import Asset
from investment.crypto.domain.portfolio import PortfolioPurpose


@dataclass(frozen=True, slots=True)
class PaperPosition:
    asset: Asset
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class PaperPortfolioSnapshot:
    portfolio_id: str
    purpose: PortfolioPurpose
    cash_asset: Asset
    cash_balance: Decimal
    positions: tuple[PaperPosition, ...]
    updated_at: datetime
