from datetime import UTC, datetime
from decimal import Decimal

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.domain.universe import UniverseMember, UniverseSnapshot
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.upbit import RawUpbitCandleBatch, UpbitTickerRecord


class AdvancingClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StubUpbitClient:
    source = "upbit"

    def __init__(self, clock: AdvancingClock) -> None:
        self.clock = clock
        self.requested: list[str] = []

    def rank_markets_by_quote_volume(
        self, markets: tuple[str, ...]
    ) -> tuple[UpbitTickerRecord, ...]:
        return tuple(
            UpbitTickerRecord(market, Decimal(str(1000 - index)))
            for index, market in enumerate(sorted(markets))
        )

    def fetch_minute_candles(self, pair, start, end, timeframe):  # type: ignore[no-untyped-def]
        self.requested.append(pair.symbol)
        self.clock.now += 181.0
        return RawUpbitCandleBatch(pair, start, end, end, self.source, (), timeframe)


def test_liquid_universe_sync_defers_unstarted_assets_after_time_budget(tmp_path) -> None:
    observed_at = datetime(2026, 9, 3, tzinfo=UTC)
    universe = build_universe(("AAA/KRW", "BBB/KRW", "CCC/KRW"))
    snapshot = UniverseSnapshot(
        observed_at,
        "upbit",
        tuple(
            UniverseMember(pair, False, "upbit", observed_at) for pair in universe.pairs
        ),
    )
    clock = AdvancingClock()
    client = StubUpbitClient(clock)
    service = CryptoIntradayMarketDataService(
        client,  # type: ignore[arg-type]
        CryptoRawCandleStorage(tmp_path / "raw"),
        CryptoCandleParquetStorage(tmp_path / "normalized"),
    )

    results = service.sync_liquid_universe(
        snapshot,
        observed_at,
        datetime(2026, 9, 3, 1, tzinfo=UTC),
        maximum_assets=3,
        timeframe=CandleTimeframe.MINUTE_15,
        throttle_seconds=0,
        maximum_duration_seconds=180,
        monotonic=clock,
    )

    assert client.requested == ["AAAKRW"]
    assert [result.status for result in results] == ["COMPLETED", "DEFERRED", "DEFERRED"]
    assert all("time budget exhausted" in (item.error or "") for item in results[1:])


def test_liquid_universe_sync_rejects_nonpositive_time_budget(tmp_path) -> None:
    observed_at = datetime(2026, 9, 3, tzinfo=UTC)
    snapshot = UniverseSnapshot(observed_at, "upbit", ())
    clock = AdvancingClock()
    service = CryptoIntradayMarketDataService(
        StubUpbitClient(clock),  # type: ignore[arg-type]
        CryptoRawCandleStorage(tmp_path / "raw"),
        CryptoCandleParquetStorage(tmp_path / "normalized"),
    )

    try:
        service.sync_liquid_universe(
            snapshot,
            observed_at,
            datetime(2026, 9, 3, 1, tzinfo=UTC),
            maximum_duration_seconds=0,
        )
    except ValueError as error:
        assert str(error) == "maximum_duration_seconds must be positive"
    else:
        raise AssertionError("nonpositive sync budget must be rejected")
