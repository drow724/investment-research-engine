"""Persistent paper-accounting read models."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.domain.market import Asset
from investment.crypto.domain.order import OrderSide
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


@dataclass(frozen=True, slots=True)
class PaperExecutionRecord:
    order_id: str
    intent_id: str
    portfolio_id: str
    pair: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class PaperRebalanceDecisionRecord:
    decision_id: str
    portfolio_id: str
    strategy_version: str
    as_of: datetime
    universe_observed_at: datetime
    execute: bool
    equity: Decimal
    assessments_json: str
    selected_json: str
    orders_json: str
    risk_violations: tuple[str, ...]
    decision_reasons: tuple[str, ...]
    status: str
    created_at: datetime
