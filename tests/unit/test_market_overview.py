from datetime import UTC, datetime

from investment.crypto.application.market_overview_service import CryptoMarketOverviewService
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from tests.crypto_fixtures import crypto_bundle


def test_latest_market_overview_uses_only_available_candles() -> None:
    bundle = crypto_bundle(30)
    service = CryptoMarketOverviewService(InMemoryCryptoMarketDataProvider(bundle))

    prices = service.latest(
        ("BTC/KRW", "ETH/KRW", "SOL/KRW"),
        datetime(2023, 1, 20, tzinfo=UTC),
    )

    assert len(prices) == 3
    assert all(item.as_of <= datetime(2023, 1, 20, tzinfo=UTC) for item in prices)
    assert all(item.daily_change is not None for item in prices)
