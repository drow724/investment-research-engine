"""Order intentions and approvals remain separate from exchange SDK objects."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import TradingPair
from investment.crypto.domain.portfolio import PortfolioPurpose


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    portfolio_id: str
    purpose: PortfolioPurpose
    pair: TradingPair
    side: OrderSide
    quantity: Decimal
    created_at: datetime
    intent_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_utc(self.created_at, "created_at"))
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if not self.intent_id.strip():
            raise ValueError("intent_id is required for idempotency")


@dataclass(frozen=True, slots=True)
class ApprovedOrder:
    intent: OrderIntent
    risk_policy_version: str

    def __post_init__(self) -> None:
        if self.intent.purpose is PortfolioPurpose.CORE_INVESTMENT:
            raise ValueError("the crypto trading engine cannot approve core investment orders")
