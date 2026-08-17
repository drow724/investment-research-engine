"""Deterministic full-fill paper gateway; it cannot access real exchange credentials."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from investment.crypto.domain.order import ApprovedOrder
from investment.crypto.ports.exchange import ExecutionReport


class PaperExchangeGateway:
    def __init__(
        self,
        prices: Mapping[str, Decimal],
        fee_rate: Decimal = Decimal("0.0005"),
    ) -> None:
        self._prices = dict(prices)
        self._fee_rate = fee_rate
        self._reports: dict[str, ExecutionReport] = {}

    def submit(self, order: ApprovedOrder) -> ExecutionReport:
        if order.intent.intent_id in self._reports:
            return self._reports[order.intent.intent_id]
        symbol = order.intent.pair.symbol
        if symbol not in self._prices:
            raise ValueError(f"paper price unavailable for {symbol}")
        price = self._prices[symbol]
        notional = price * order.intent.quantity
        report = ExecutionReport(
            order_id=f"paper:{order.intent.intent_id}",
            status="FILLED",
            side=order.intent.side,
            filled_quantity=order.intent.quantity,
            average_price=price,
            fee=notional * self._fee_rate,
            executed_at=datetime.now(UTC),
        )
        self._reports[order.intent.intent_id] = report
        return report


class PaperExchangeGatewayFactory:
    def __init__(self, fee_rate: Decimal = Decimal("0.0005")) -> None:
        self.fee_rate = fee_rate

    def create(self, prices: Mapping[str, Decimal]) -> PaperExchangeGateway:
        return PaperExchangeGateway(prices, self.fee_rate)
