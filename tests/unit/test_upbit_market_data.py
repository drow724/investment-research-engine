from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.upbit import (
    UpbitDailyCandleNormalizer,
    UpbitMinuteCandleNormalizer,
    UpbitPublicClient,
    UpbitRateLimiter,
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


def test_upbit_tickers_are_ranked_by_24h_quote_volume() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/ticker"
        return httpx.Response(
            200,
            json=[
                {"market": "KRW-ETH", "acc_trade_price_24h": 100},
                {"market": "KRW-BTC", "acc_trade_price_24h": 300},
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = UpbitPublicClient("https://upbit.test", client).rank_markets_by_quote_volume(
            ("KRW-ETH", "KRW-BTC")
        )
    assert [item.market for item in result] == ["KRW-BTC", "KRW-ETH"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_upbit_client_retries_429_with_backoff() -> None:
    attempts = 0
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429, request=request, headers={"Remaining-Req": "group=candle; sec=0"}
            )
        return httpx.Response(200, request=request, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = UpbitPublicClient(
            "https://upbit.test",
            client,
            rate_limiter=UpbitRateLimiter(
                requests_per_second=8, sleeper=clock.sleep, clock=clock.monotonic
            ),
            jitter=lambda: 0.0,
        ).list_markets()

    assert result == ()
    assert attempts == 2
    assert sum(clock.sleeps) >= 1.0


def test_upbit_client_honors_zero_remaining_requests_before_next_call() -> None:
    clock = FakeClock()
    responses = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal responses
        responses += 1
        headers = {"Remaining-Req": "group=market; min=600; sec=0"} if responses == 1 else {}
        return httpx.Response(200, request=request, headers=headers, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        upbit = UpbitPublicClient(
            "https://upbit.test",
            client,
            rate_limiter=UpbitRateLimiter(
                requests_per_second=8, sleeper=clock.sleep, clock=clock.monotonic
            ),
        )
        upbit.list_markets()
        upbit.list_markets()

    assert sum(clock.sleeps) >= 1.05


def test_upbit_client_does_not_retry_temporary_418_block() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(418, request=request, json={"error": "temporarily blocked"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        UpbitPublicClient("https://upbit.test", client).list_markets()

    assert attempts == 1


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
    assert normalized.row_count(pair) == 1


def test_upbit_minute_candles_are_available_only_after_bar_close(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/candles/minutes/15"
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "candle_date_time_utc": "2024-01-01T00:00:00",
                    "opening_price": 100,
                    "high_price": 110,
                    "low_price": 90,
                    "trade_price": 105,
                    "candle_acc_trade_volume": 3,
                }
            ],
        )

    pair = build_universe(("BTC/KRW",)).pairs[0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch = UpbitPublicClient("https://upbit.test", client).fetch_minute_candles(
            pair,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 30, tzinfo=UTC),
            CandleTimeframe.MINUTE_15,
        )
    candles = UpbitMinuteCandleNormalizer().normalize(batch)
    assert candles[0].available_at == datetime(2024, 1, 1, 0, 15, tzinfo=UTC)

    path = CryptoCandleParquetStorage(tmp_path / "normalized").save(
        pair,
        candles,
        source="upbit",
        ingested_at=batch.ingested_at,
        timeframe=CandleTimeframe.MINUTE_15,
    )
    assert path == tmp_path / "normalized" / "15m" / "BTCKRW.parquet"
