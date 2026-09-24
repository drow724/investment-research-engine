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
        slippage_rate: Decimal = Decimal("0"),
        execution_model_version: str = "paper-fill-v1",
    ) -> None:
        self._prices = dict(prices)
        self._fee_rate = fee_rate
        self._slippage_rate = slippage_rate
        self._execution_model_version = execution_model_version.strip()
        if self._execution_model_version not in {"paper-fill-v1", "paper-fill-v2"}:
            raise ValueError("unsupported paper execution model")
        if min(self._fee_rate, self._slippage_rate) < 0:
            raise ValueError("paper fee and slippage rates cannot be negative")
        if self._execution_model_version == "paper-fill-v1" and self._slippage_rate != 0:
            raise ValueError("paper-fill-v1 does not apply slippage")
        self._reports: dict[str, ExecutionReport] = {}

    def submit(self, order: ApprovedOrder) -> ExecutionReport:
        if order.intent.intent_id in self._reports:
            return self._reports[order.intent.intent_id]
        symbol = order.intent.pair.symbol
        if symbol not in self._prices:
            raise ValueError(f"paper price unavailable for {symbol}")
        reference_price = self._prices[symbol]
        if self._execution_model_version == "paper-fill-v2":
            direction = Decimal("1") if order.intent.side.value == "BUY" else Decimal("-1")
            price = reference_price * (Decimal("1") + direction * self._slippage_rate)
        else:
            price = reference_price
        notional = price * order.intent.quantity
        report = ExecutionReport(
            order_id=f"paper:{order.intent.intent_id}",
            status="FILLED",
            side=order.intent.side,
            filled_quantity=order.intent.quantity,
            average_price=price,
            fee=notional * self._fee_rate,
            executed_at=datetime.now(UTC),
            execution_model_version=self._execution_model_version,
            fee_rate=self._fee_rate,
            slippage_rate=self._slippage_rate,
        )
        self._reports[order.intent.intent_id] = report
        return report


class PaperExchangeGatewayFactory:
    def __init__(
        self,
        fee_rate: Decimal = Decimal("0.0005"),
        slippage_rate: Decimal = Decimal("0"),
        execution_model_version: str = "paper-fill-v1",
    ) -> None:
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.execution_model_version = execution_model_version

    def create(self, prices: Mapping[str, Decimal]) -> PaperExchangeGateway:
        return PaperExchangeGateway(
            prices,
            self.fee_rate,
            self.slippage_rate,
            self.execution_model_version,
        )
