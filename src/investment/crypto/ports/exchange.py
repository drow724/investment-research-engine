from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from investment.core.domain.observation import require_utc
from investment.crypto.domain.order import ApprovedOrder, OrderSide


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    order_id: str
    status: str
    side: OrderSide
    filled_quantity: Decimal
    average_price: Decimal
    fee: Decimal
    executed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "executed_at", require_utc(self.executed_at, "executed_at"))
        if self.filled_quantity <= 0 or self.average_price <= 0 or self.fee < 0:
            raise ValueError("execution quantity/price must be positive and fee non-negative")


class ExchangeGateway(Protocol):
    def submit(self, order: ApprovedOrder) -> ExecutionReport: ...


class PaperExchangeGatewayFactory(Protocol):
    def create(self, prices: Mapping[str, Decimal]) -> ExchangeGateway: ...
