from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment.crypto.domain.market import Asset, AssetKind, TradingPair
from investment.crypto.domain.order import OrderIntent, OrderSide
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGateway
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy


def test_paper_gateway_accepts_only_risk_approved_order() -> None:
    pair = TradingPair(Asset("BTC"), Asset("KRW", AssetKind.CASH))
    intent = OrderIntent(
        "paper",
        PortfolioPurpose.PAPER_TRADING,
        pair,
        OrderSide.BUY,
        Decimal("0.001"),
        datetime(2025, 1, 1, tzinfo=UTC),
        "intent-paper-buy-1",
    )
    risk = DeterministicRiskEngine(RiskPolicy(minimum_order_notional=Decimal("5000")))
    approved = risk.approve_order(intent, Decimal("50000000"))
    report = PaperExchangeGateway({"BTCKRW": Decimal("50000000")}).submit(approved)
    assert report.status == "FILLED"
    assert report.filled_quantity == Decimal("0.001")


def test_core_investment_order_is_rejected_before_exchange() -> None:
    pair = TradingPair(Asset("BTC"), Asset("KRW", AssetKind.CASH))
    intent = OrderIntent(
        "core-btc",
        PortfolioPurpose.CORE_INVESTMENT,
        pair,
        OrderSide.SELL,
        Decimal("0.001"),
        datetime(2025, 1, 1, tzinfo=UTC),
        "intent-core-sell-1",
    )
    with pytest.raises(ValueError, match="core investment"):
        DeterministicRiskEngine(RiskPolicy()).approve_order(intent, Decimal("50000000"))
