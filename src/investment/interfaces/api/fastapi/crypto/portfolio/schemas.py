from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

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
