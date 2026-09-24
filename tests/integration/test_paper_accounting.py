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


def test_paper_fill_v2_applies_adverse_slippage_and_persists_execution_semantics(
    tmp_path,
) -> None:
    repository = SqlitePaperPortfolioRepository(tmp_path / "paper-v2.sqlite3")
    repository.create(
        TradingPortfolio(
            "paper-v2",
            PortfolioPurpose.PAPER_TRADING,
            Asset("KRW", AssetKind.CASH),
            Decimal("1000"),
        )
    )
    pair = build_universe(("BTC/KRW",)).pairs[0]
    risk = DeterministicRiskEngine(
        RiskPolicy(
            minimum_order_notional=Decimal("0"), maximum_single_order_notional=Decimal("1000")
        )
    )
    gateway = PaperExchangeGateway(
        {"BTCKRW": Decimal("100")},
        fee_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.0005"),
        execution_model_version="paper-fill-v2",
    )
    buy = risk.approve_order(
        OrderIntent(
            "paper-v2",
            PortfolioPurpose.PAPER_TRADING,
            pair,
            OrderSide.BUY,
            Decimal("1"),
            datetime(2025, 1, 1, tzinfo=UTC),
            "paper-v2-buy",
        ),
        Decimal("100"),
    )
    buy_report = gateway.submit(buy)
    assert buy_report.average_price == Decimal("100.0500")
    assert buy_report.fee == Decimal("0.05002500")
    assert repository.apply_execution(buy, buy_report)
    assert not repository.apply_execution(buy, buy_report)
    after_buy = repository.get("paper-v2")
    assert after_buy.cash_balance == Decimal("899.89997500")
    assert after_buy.positions[0].average_cost == Decimal("100.10002500")

    sell = risk.approve_order(
        OrderIntent(
            "paper-v2",
            PortfolioPurpose.PAPER_TRADING,
            pair,
            OrderSide.SELL,
            Decimal("1"),
            datetime(2025, 1, 2, tzinfo=UTC),
            "paper-v2-sell",
        ),
        Decimal("100"),
    )
    sell_report = gateway.submit(sell)
    assert sell_report.average_price == Decimal("99.9500")
    assert sell_report.fee == Decimal("0.04997500")
    assert repository.apply_execution(sell, sell_report)
    execution = repository.list_executions("paper-v2", 1)[0]
    assert execution.realized_pnl == Decimal("-0.20000000")
    assert execution.execution_model_version == "paper-fill-v2"
    assert execution.fee_rate == Decimal("0.0005")
    assert execution.slippage_rate == Decimal("0.0005")
    assert repository.get("paper-v2").cash_balance == Decimal("999.80000000")
