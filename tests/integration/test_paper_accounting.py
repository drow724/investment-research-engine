from datetime import UTC, datetime
from decimal import Decimal

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.domain.market import Asset, AssetKind
from investment.crypto.domain.order import OrderIntent, OrderSide
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGateway
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy


def test_paper_execution_is_persistent_and_idempotent(tmp_path) -> None:
    repository = SqlitePaperPortfolioRepository(tmp_path / "paper.sqlite3")
    repository.create(
        TradingPortfolio(
            "paper-1",
            PortfolioPurpose.PAPER_TRADING,
            Asset("KRW", AssetKind.CASH),
            Decimal("100000"),
        )
    )
    pair = build_universe(("BTC/KRW",)).pairs[0]
    intent = OrderIntent(
        "paper-1",
        PortfolioPurpose.PAPER_TRADING,
        pair,
        OrderSide.BUY,
        Decimal("0.001"),
        datetime(2025, 1, 1, tzinfo=UTC),
        "paper-buy-1",
    )
    approved = DeterministicRiskEngine(
        RiskPolicy(minimum_order_notional=Decimal("5000"))
    ).approve_order(intent, Decimal("50000000"))
    service = PaperTradingService(PaperExchangeGateway({"BTCKRW": Decimal("50000000")}), repository)
    report, first_applied = service.execute(approved)
    same_report, second_applied = service.execute(approved)
    snapshot = SqlitePaperPortfolioRepository(tmp_path / "paper.sqlite3").get("paper-1")
    assert first_applied
    assert not second_applied
    assert same_report == report
    assert snapshot.cash_balance == Decimal("49975.0000000")
    assert snapshot.positions[0].quantity == Decimal("0.001")
    assert snapshot.positions[0].average_cost == Decimal("50025000.0000")

    sell_intent = OrderIntent(
        "paper-1",
        PortfolioPurpose.PAPER_TRADING,
        pair,
        OrderSide.SELL,
        Decimal("0.0004"),
        datetime(2025, 1, 2, tzinfo=UTC),
        "paper-sell-1",
    )
    sell_approved = DeterministicRiskEngine(
        RiskPolicy(minimum_order_notional=Decimal("5000"))
    ).approve_order(sell_intent, Decimal("60000000"))
    sell_service = PaperTradingService(
        PaperExchangeGateway({"BTCKRW": Decimal("60000000")}), repository
    )
    _, sell_applied = sell_service.execute(sell_approved)
    after_sell = repository.get("paper-1")
    assert sell_applied
    assert after_sell.cash_balance == Decimal("73963.00000000")
    assert after_sell.positions[0].quantity == Decimal("0.0006")
