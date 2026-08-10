from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from investment.crypto.application.backtest_service import CryptoBacktestService
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.interfaces.api.fastapi.dependencies import get_crypto_backtest_service
from investment.interfaces.api.fastapi.main import create_app
from tests.crypto_fixtures import crypto_bundle


def test_crypto_backtest_api_is_a_separate_serialization_adapter() -> None:
    bundle = crypto_bundle()
    app = create_app()
    app.dependency_overrides[get_crypto_backtest_service] = lambda: CryptoBacktestService(
        InMemoryCryptoMarketDataProvider(bundle)
    )
    client = TestClient(app)
    start: datetime = bundle.candles["BTCKRW"][220].open_time
    end: datetime = bundle.candles["BTCKRW"][260].open_time
    response = client.post(
        "/api/v1/crypto/backtests",
        json={
            "pairs": ["BTC/KRW", "ETH/KRW", "SOL/KRW"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initialCapital": str(Decimal("100000")),
            "portfolioPurpose": "PAPER_TRADING",
            "lookbackDays": 30,
            "maximumPositions": 3,
            "rebalanceDays": 7,
        },
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["engine"] == "crypto_trading"
    assert body["portfolioPurpose"] == "PAPER_TRADING"
    assert body["metrics"]["cashBenchmarkReturn"] == 0.0
    assert body["rebalanceCount"] > 0


def test_crypto_api_contract_cannot_accept_core_investment_purpose() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/crypto/backtests",
        json={
            "pairs": ["BTC/KRW"],
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z",
            "portfolioPurpose": "CORE_INVESTMENT",
        },
    )
    assert response.status_code == 422
