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
    execution_model_version: str = "paper-fill-v1"
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "executed_at", require_utc(self.executed_at, "executed_at"))
        if self.filled_quantity <= 0 or self.average_price <= 0 or self.fee < 0:
            raise ValueError("execution quantity/price must be positive and fee non-negative")
        if not self.execution_model_version.strip():
            raise ValueError("execution model version is required")
        if min(self.fee_rate, self.slippage_rate) < 0:
            raise ValueError("execution fee and slippage rates cannot be negative")


class ExchangeGateway(Protocol):
    def submit(self, order: ApprovedOrder) -> ExecutionReport: ...


class PaperExchangeGatewayFactory(Protocol):
    def create(self, prices: Mapping[str, Decimal]) -> ExchangeGateway: ...
