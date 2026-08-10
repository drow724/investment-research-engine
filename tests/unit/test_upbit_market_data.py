from datetime import UTC, datetime
from decimal import Decimal

import httpx

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.upbit import (
    UpbitDailyCandleNormalizer,
    UpbitPublicClient,
)


def test_upbit_client_and_normalizer_use_next_day_availability() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "candle_date_time_utc": "2024-01-02T00:00:00",
                    "opening_price": 100,
                    "high_price": 120,
                    "low_price": 90,
                    "trade_price": 110,
                    "candle_acc_trade_volume": 5,
                },
                {
                    "market": "KRW-BTC",
                    "candle_date_time_utc": "2024-01-01T00:00:00",
                    "opening_price": 90,
                    "high_price": 105,
                    "low_price": 80,
                    "trade_price": 100,
                    "candle_acc_trade_volume": 4,
                },
            ],
        )

    pair = build_universe(("BTC/KRW",)).pairs[0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch = UpbitPublicClient("https://upbit.test", client).fetch_daily_candles(
            pair,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 3, tzinfo=UTC),
        )
    candles = UpbitDailyCandleNormalizer().normalize(batch)
    assert requests[0].url.params["market"] == "KRW-BTC"
    assert [candle.close for candle in candles] == [Decimal("100"), Decimal("110")]
    assert candles[0].available_at == datetime(2024, 1, 2, tzinfo=UTC)


def test_upbit_market_discovery_preserves_warning_flag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "korean_name": "비트코인",
                    "english_name": "Bitcoin",
                    "market_event": {"warning": True},
                }
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = UpbitPublicClient("https://upbit.test", client).list_markets()
    assert result[0].market == "KRW-BTC"
    assert result[0].warning


def test_crypto_candle_storage_is_idempotent(tmp_path) -> None:
    pair = build_universe(("BTC/KRW",)).pairs[0]
    payload = {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2024-01-01T00:00:00",
        "opening_price": 90,
        "high_price": 105,
        "low_price": 80,
        "trade_price": 100,
        "candle_acc_trade_volume": 4,
    }
    from investment.crypto.infrastructure.upbit import RawUpbitCandleBatch

    batch = RawUpbitCandleBatch(
        pair,
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 3, tzinfo=UTC),
        "upbit",
        [payload],
    )
    raw = CryptoRawCandleStorage(tmp_path / "raw")
    assert raw.save(batch) == raw.save(batch)
    candles = UpbitDailyCandleNormalizer().normalize(batch)
    normalized = CryptoCandleParquetStorage(tmp_path / "normalized")
    normalized.save(pair, candles, source="upbit", ingested_at=batch.ingested_at)
    normalized.save(pair, candles, source="upbit", ingested_at=batch.ingested_at)
    import polars as pl

    assert pl.read_parquet(tmp_path / "normalized" / "BTCKRW.parquet").height == 1
