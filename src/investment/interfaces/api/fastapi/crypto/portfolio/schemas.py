from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from investment.interfaces.api.fastapi.crypto.backtest.schemas import CryptoApiModel


class CreatePaperPortfolioRequest(CryptoApiModel):
    portfolio_id: str = Field(min_length=1, max_length=100)
    purpose: Literal["PAPER_TRADING", "SYSTEMATIC_TRADING"] = "PAPER_TRADING"
    cash_asset: str = Field(default="KRW", pattern=r"^[A-Z0-9]{2,10}$")
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)


class PaperPositionResponse(CryptoApiModel):
    asset: str
    quantity: Decimal
    average_cost: Decimal


class PaperPortfolioResponse(CryptoApiModel):
    portfolio_id: str
    purpose: str
    cash_asset: str
    cash_balance: Decimal
    positions: tuple[PaperPositionResponse, ...]
    updated_at: datetime


class PaperExecutionResponse(CryptoApiModel):
    order_id: str
    intent_id: str
    pair: str
    side: str
    quantity: str
    price: str
    notional: str
    fee: str
    realized_pnl: str
    executed_at: datetime


class PaperRebalanceDecisionResponse(CryptoApiModel):
    decision_id: str
    strategy_version: str
    as_of: datetime
    universe_observed_at: datetime
    execute: bool
    equity: str
    assessments: list[dict[str, object]]
    selected: list[dict[str, object]]
    orders: list[dict[str, object]]
    risk_violations: tuple[str, ...]
    decision_reasons: tuple[str, ...]
    status: str
    created_at: datetime


class DynamicRebalanceRequest(CryptoApiModel):
    portfolio_id: str = Field(min_length=1, max_length=100)
    as_of: datetime
    execute: bool = False

    @field_validator("as_of")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("rebalance timestamp must be timezone-aware")
        return value


class CandidateAssessmentResponse(CryptoApiModel):
    pair: str
    eligible: bool
    reason: str
    score: float | None
    average_quote_volume: str | None
    latest_price: str | None


class SelectedAssetResponse(CryptoApiModel):
    pair: str
    score: float
    target_weight: str
    reason: str


class RebalanceOrderResponse(CryptoApiModel):
    intent_id: str
    pair: str
    side: str
    quantity: str
    reference_price: str
    notional: str
    current_weight: str
    target_weight: str
    status: str


class DynamicRebalanceResponse(CryptoApiModel):
    portfolio_id: str
    as_of: datetime
    universe_observed_at: datetime
    dry_run: bool
    equity: str
    selected: tuple[SelectedAssetResponse, ...]
    assessments: tuple[CandidateAssessmentResponse, ...]
    orders: tuple[RebalanceOrderResponse, ...]
    final_portfolio: PaperPortfolioResponse
    risk_violations: tuple[str, ...] = ()
    decision_reasons: tuple[str, ...] = ()
