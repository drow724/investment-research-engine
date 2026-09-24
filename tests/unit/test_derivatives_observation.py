import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest

from investment.crypto.derivatives.binance import BinanceIntradayDerivativesClient
from investment.crypto.derivatives.domain import (
    BasisSource,
    CoinbasePriceObservation,
    DerivativesSnapshot,
    LiquidatedPosition,
    SqueezeSignal,
    SqueezeState,
    StreamState,
)
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.derivatives.service import (
    BtcSqueezeBasisProxySignalCalculator,
    BtcSqueezeSignalCalculator,
    DerivativesObservationService,
)
from investment.crypto.derivatives.streams import (
    BINANCE_LIQUIDATION_STREAM,
    DerivativesMarketStreams,
    DirectoryStreamOwnershipLock,
    is_coinbase_heartbeat,
    message_is_stale,
    parse_binance_liquidation,
    parse_coinbase_prices,
)
from investment.crypto.domain.market import Asset, AssetKind, MarketCandle, TradingPair


def test_binance_intraday_derivatives_client_maps_public_metrics() -> None:
    timestamp = 1_767_226_800_000

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100000",
                    "indexPrice": "99900",
                    "lastFundingRate": "-0.0001",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "1000", "time": timestamp})
        common = {"timestamp": timestamp}
        if path.endswith("basis"):
            return httpx.Response(200, json=[{**common, "basisRate": "-0.001"}])
        if path.endswith("globalLongShortAccountRatio"):
            return httpx.Response(200, json=[{**common, "longShortRatio": "0.8"}])
        if path.endswith("topLongShortPositionRatio"):
            return httpx.Response(200, json=[{**common, "longShortRatio": "0.9"}])
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{**common, "buySellRatio": "1.2"}])
        raise AssertionError(path)

    as_of = datetime.fromtimestamp(timestamp / 1000, UTC)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = BinanceIntradayDerivativesClient(client=client).fetch_snapshot(as_of)

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.open_interest_usd == Decimal("100000000")
    assert snapshot.funding_rate == Decimal("-0.0001")
    assert snapshot.basis_rate == Decimal("-0.001")
    assert snapshot.taker_buy_sell_ratio == Decimal("1.2")


def test_empty_binance_basis_is_retried_then_recorded_as_missing_data() -> None:
    timestamp = 1_767_226_800_000
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100",
                    "indexPrice": "100",
                    "lastFundingRate": "0",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "10", "time": timestamp})
        if path.endswith("basis"):
            calls += 1
            return httpx.Response(200, json=[])
        common = {"timestamp": timestamp, "longShortRatio": "1"}
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": timestamp, "buySellRatio": "1"}])
        return httpx.Response(200, json=[common])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = BinanceIntradayDerivativesClient(
            client=client, sleeper=sleeps.append
        ).fetch_snapshot(datetime.fromtimestamp(timestamp / 1000, UTC))

    assert calls == 3
    assert sleeps == [0.2, 0.4]
    assert snapshot.basis_rate is None
    assert snapshot.missing_fields == frozenset({"basis_rate", "coinbase_premium_rate"})


@pytest.mark.parametrize("status_code", [418, 429])
def test_binance_basis_rate_limit_is_missing_until_retry_after_expires(
    status_code: int, caplog: pytest.LogCaptureFixture
) -> None:
    timestamp = 1_767_226_800_000
    now = [datetime.fromtimestamp(timestamp / 1000, UTC)]
    basis_calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal basis_calls
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100",
                    "indexPrice": "100",
                    "lastFundingRate": "0",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "10", "time": timestamp})
        if path.endswith("basis"):
            basis_calls += 1
            if basis_calls == 1:
                return httpx.Response(
                    status_code,
                    headers={"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": "42"},
                    json={"code": -1003, "msg": "Too many requests"},
                )
            return httpx.Response(200, json=[{"timestamp": timestamp, "basisRate": "-0.001"}])
        common = {"timestamp": timestamp, "longShortRatio": "1"}
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": timestamp, "buySellRatio": "1"}])
        return httpx.Response(200, json=[common])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = BinanceIntradayDerivativesClient(
            client=client,
            clock=lambda: now[0],
            sleeper=sleeps.append,
        )
        first = adapter.fetch_snapshot(now[0])
        second = adapter.fetch_snapshot(now[0])
        now[0] += timedelta(seconds=121)
        recovered = adapter.fetch_snapshot(now[0])

    assert first.basis_rate is None
    assert second.basis_rate is None
    assert "basis_rate" in first.missing_fields
    assert basis_calls == 2
    assert sleeps == []
    assert recovered.basis_rate == Decimal("-0.001")
    assert "recording MISSING_DATA" in caplog.text
    assert "used_weight=x-mbx-used-weight-1m=42" in caplog.text


def test_binance_required_metric_rate_limit_still_fails_closed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            429,
            headers={"Retry-After": "60"},
            json={"code": -1003, "msg": "Too many requests"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        BinanceIntradayDerivativesClient(client=client).fetch_snapshot(
            datetime(2026, 1, 1, tzinfo=UTC)
        )

    assert calls == ["/fapi/v1/premiumIndex"]


@pytest.mark.parametrize("status_code", [200, 418, 429])
def test_binance_basis_rate_limit_uses_payload_ban_deadline_for_every_http_status(
    status_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    timestamp = 1_767_226_800_000
    now = [datetime.fromtimestamp(timestamp / 1000, UTC)]
    banned_until = now[0] + timedelta(minutes=5)
    basis_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal basis_calls
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100",
                    "indexPrice": "100",
                    "lastFundingRate": "0",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "10", "time": timestamp})
        if path.endswith("basis"):
            basis_calls += 1
            if basis_calls == 1:
                return httpx.Response(
                    status_code,
                    json={
                        "code": -1003,
                        "msg": (
                            "Way too many requests; IP(10.0.0.1) banned until "
                            f"{int(banned_until.timestamp() * 1000)}."
                        ),
                    },
                )
            return httpx.Response(200, json=[{"timestamp": timestamp, "basisRate": "-0.001"}])
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": timestamp, "buySellRatio": "1"}])
        return httpx.Response(200, json=[{"timestamp": timestamp, "longShortRatio": "1"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = BinanceIntradayDerivativesClient(client=client, clock=lambda: now[0])
        first = adapter.fetch_snapshot(now[0])
        now[0] = banned_until - timedelta(seconds=1)
        second = adapter.fetch_snapshot(now[0])
        now[0] = banned_until + timedelta(seconds=1)
        recovered = adapter.fetch_snapshot(now[0])

    assert first.basis_rate is None
    assert second.basis_rate is None
    assert recovered.basis_rate == Decimal("-0.001")
    assert basis_calls == 2
    assert "deadline_source=payload" in caplog.text
    assert f"blocked_until={banned_until.isoformat()}" in caplog.text
    assert "code=-1003" in caplog.text


@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_message", "expected_type"),
    [
        ({"code": -1008, "msg": "Request throttled"}, "-1008", "Request throttled", "dict"),
        (["schema changed"], "unknown", "unexpected response", "list"),
        (b"not-json", "unknown", "unparseable response", "unparseable"),
    ],
)
def test_malformed_binance_basis_is_logged_and_recorded_as_missing_data(
    payload: object,
    expected_code: str,
    expected_message: str,
    expected_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    timestamp = 1_767_226_800_000

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100",
                    "indexPrice": "100",
                    "lastFundingRate": "0",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "10", "time": timestamp})
        if path.endswith("basis"):
            if isinstance(payload, bytes):
                return httpx.Response(
                    200,
                    headers={
                        "Content-Type": "application/json",
                        "X-MBX-USED-WEIGHT-1M": "7",
                    },
                    content=payload,
                )
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "X-MBX-USED-WEIGHT-1M": "7",
                },
                json=payload,
            )
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": timestamp, "buySellRatio": "1"}])
        return httpx.Response(200, json=[{"timestamp": timestamp, "longShortRatio": "1"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = BinanceIntradayDerivativesClient(client=client).fetch_snapshot(
            datetime.fromtimestamp(timestamp / 1000, UTC)
        )

    assert snapshot.basis_rate is None
    assert "basis_rate" in snapshot.missing_fields
    assert "recording MISSING_DATA" in caplog.text
    assert f"payload_type={expected_type}" in caplog.text
    assert f"code={expected_code}" in caplog.text
    assert f"message={expected_message}" in caplog.text
    assert "used_weight=x-mbx-used-weight-1m=7" in caplog.text


def test_binance_retry_completion_time_is_snapshot_availability_time() -> None:
    timestamp = 1_767_226_800_000
    now = [datetime.fromtimestamp(timestamp / 1000, UTC)]

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100",
                    "indexPrice": "100",
                    "lastFundingRate": "0",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "10", "time": timestamp})
        if path.endswith("basis"):
            return httpx.Response(200, json=[])
        if path.endswith("takerlongshortRatio"):
            return httpx.Response(200, json=[{"timestamp": timestamp, "buySellRatio": "1"}])
        return httpx.Response(200, json=[{"timestamp": timestamp, "longShortRatio": "1"}])

    started_at = now[0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = BinanceIntradayDerivativesClient(
            client=client, clock=lambda: now[0], sleeper=sleep
        ).fetch_snapshot()

    assert snapshot.available_at == started_at + timedelta(seconds=0.6)


def test_missing_basis_excludes_snapshot_from_fuel_score() -> None:
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * index), Decimal("100"), Decimal("100"))
        for index in range(5)
    )
    latest = replace(snapshots[0], basis_rate=None, missing_fields=frozenset({"basis_rate"}))
    signal = BtcSqueezeSignalCalculator().calculate((*snapshots[1:], latest))
    assert signal.state is SqueezeState.INSUFFICIENT_DATA
    assert "MISSING_DATA_BASIS_RATE" in signal.evidence


def test_basis_proxy_calculator_keeps_official_gap_explicit_and_scores_with_proxy() -> None:
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * index), Decimal("100"), Decimal("100"))
        for index in range(5)
    )
    latest = replace(
        snapshots[0],
        mark_price=Decimal("99.9"),
        index_price=Decimal("100"),
        basis_rate=None,
        missing_fields=frozenset({"basis_rate"}),
    )

    signal = BtcSqueezeBasisProxySignalCalculator().calculate((*snapshots[1:], latest))

    assert latest.basis_rate is None
    assert latest.mark_index_basis_rate == Decimal("-0.001")
    assert signal.feature_version == "btc-squeeze-v3-mark-index-basis-proxy"
    assert signal.state is not SqueezeState.INSUFFICIENT_DATA
    assert signal.basis_input_rate == Decimal("-0.001")
    assert signal.basis_input_source is BasisSource.MARK_INDEX_PROXY
    assert "BASIS_INPUT_MARK_INDEX_PROXY" in signal.evidence


def test_basis_proxy_calculator_never_mixes_in_an_official_basis_value() -> None:
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * index), Decimal("100"), Decimal("100"))
        for index in range(5)
    )
    latest = replace(
        snapshots[0],
        mark_price=Decimal("99.9"),
        index_price=Decimal("100"),
        basis_rate=Decimal("0.02"),
    )

    signal = BtcSqueezeBasisProxySignalCalculator().calculate((*snapshots[1:], latest))

    assert signal.basis_input_rate == Decimal("-0.001")
    assert signal.basis_input_source is BasisSource.MARK_INDEX_PROXY


def test_service_persists_official_and_proxy_signals_in_parallel(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    history = tuple(
        _snapshot(as_of - timedelta(minutes=15 * (4 - index)), Decimal("100"), Decimal("100"))
        for index in range(4)
    )
    for snapshot in history:
        repository.save_snapshot(snapshot)
    current = replace(
        _snapshot(as_of, Decimal("105"), Decimal("99.9")),
        index_price=Decimal("100"),
        basis_rate=None,
        missing_fields=frozenset({"basis_rate"}),
    )

    class Client:
        @staticmethod
        def fetch_snapshot() -> DerivativesSnapshot:
            return current

    class MissingSpotData:
        @staticmethod
        def fetch(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError

    service = DerivativesObservationService(
        cast(Any, Client()),
        repository,
        cast(Any, MissingSpotData()),
        additional_calculators=(BtcSqueezeBasisProxySignalCalculator(),),
    )

    service.capture()

    official = repository.observation_known_at(
        "BTCUSDT", as_of, "btc-squeeze-v2-market-streams"
    )
    proxy = repository.observation_known_at(
        "BTCUSDT", as_of, "btc-squeeze-v3-mark-index-basis-proxy"
    )
    assert official is not None and official[1].state is SqueezeState.INSUFFICIENT_DATA
    assert proxy is not None and proxy[1].state is not SqueezeState.INSUFFICIENT_DATA
    assert proxy[1].basis_input_source is BasisSource.MARK_INDEX_PROXY


def test_auxiliary_proxy_failure_does_not_block_primary_derivatives_signal(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    history = tuple(
        _snapshot(as_of - timedelta(minutes=15 * (4 - index)), Decimal("100"), Decimal("100"))
        for index in range(4)
    )
    for snapshot in history:
        repository.save_snapshot(snapshot)
    current = _snapshot(as_of, Decimal("105"), Decimal("100"))

    class Client:
        @staticmethod
        def fetch_snapshot() -> DerivativesSnapshot:
            return current

    class MissingSpotData:
        @staticmethod
        def fetch(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError

    class FailingAuxiliaryCalculator(BtcSqueezeSignalCalculator):
        feature_version = "btc-squeeze-failing-auxiliary"

        def calculate(self, *_args: object, **_kwargs: object) -> SqueezeSignal:
            raise RuntimeError("intentional auxiliary failure")

    service = DerivativesObservationService(
        cast(Any, Client()),
        repository,
        cast(Any, MissingSpotData()),
        additional_calculators=(FailingAuxiliaryCalculator(),),
    )

    result = service.capture()

    assert result.feature_version == "btc-squeeze-v2-market-streams"
    assert repository.latest_signal("BTCUSDT", result.feature_version) == result


def test_repository_preserves_explicit_missing_basis_data(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshot = replace(
        _snapshot(as_of, Decimal("100"), Decimal("100")),
        basis_rate=None,
        missing_fields=frozenset({"basis_rate"}),
    )

    repository.save_snapshot(snapshot)

    assert repository.latest_snapshot("BTCUSDT") == snapshot


def test_repository_migrates_basis_input_provenance_on_existing_signal_table(tmp_path) -> None:
    path = tmp_path / "derivatives.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE squeeze_signal (
                snapshot_id TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                as_of TEXT NOT NULL,
                state TEXT NOT NULL,
                fuel_score REAL,
                ignition_score REAL,
                open_interest_change_15m REAL,
                open_interest_change_1h REAL,
                futures_price_change_15m REAL,
                futures_price_change_1h REAL,
                spot_return_15m REAL,
                spot_volume_ratio REAL,
                spot_breakout INTEGER,
                short_liquidation_usd_15m REAL,
                liquidation_confirmed INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, feature_version)
            );
            """
        )

    SqliteDerivativesObservationRepository(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(squeeze_signal)")}
    assert {"basis_input_rate", "basis_input_source"} <= columns


def test_squeeze_calculator_separates_fuel_and_unconfirmed_active_proxy() -> None:
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    open_interest = (Decimal("100"), Decimal("110"), Decimal("115"), Decimal("120"), Decimal("118"))
    prices = (Decimal("100"), Decimal("99"), Decimal("99"), Decimal("99"), Decimal("100"))
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * (4 - index)), interest, price)
        for index, (interest, price) in enumerate(zip(open_interest, prices, strict=True))
    )
    candles = _spot_candles(as_of)

    signal = BtcSqueezeSignalCalculator().calculate(snapshots, candles)

    assert signal.state is SqueezeState.IGNITION
    assert signal.fuel_score is not None and signal.fuel_score >= 0.5
    assert signal.ignition_score is not None and signal.ignition_score >= 0.55
    assert signal.open_interest_change_15m is not None
    assert signal.open_interest_change_15m < 0
    assert not signal.liquidation_confirmed
    assert "OI_UNWIND_PROXY_NOT_LIQUIDATION_CONFIRMED" in signal.evidence

    confirmed = BtcSqueezeSignalCalculator().calculate(
        snapshots,
        candles,
        short_liquidation_usd_15m=500_000,
        liquidation_confirmed=True,
    )
    assert confirmed.state is SqueezeState.ACTIVE
    assert confirmed.liquidation_confirmed
    assert "SHORT_LIQUIDATION_CONFIRMED" in confirmed.evidence


def test_derivatives_repository_round_trips_snapshot_and_signal(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * (4 - index)), Decimal(100 + index), Decimal(100))
        for index in range(5)
    )
    for snapshot in snapshots:
        repository.save_snapshot(snapshot)
    signal = BtcSqueezeSignalCalculator().calculate(snapshots)
    repository.save_signal(signal)

    assert repository.latest_snapshot("BTCUSDT") == snapshots[-1]
    assert repository.latest_signal("BTCUSDT", signal.feature_version) == signal
    assert len(repository.snapshots("BTCUSDT")) == 5


def test_latest_observation_never_pairs_a_signal_with_a_newer_unscored_snapshot(
    tmp_path,
) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)
    snapshots = tuple(
        _snapshot(as_of - timedelta(minutes=15 * (4 - index)), Decimal(100 + index), Decimal(100))
        for index in range(5)
    )
    for snapshot in snapshots:
        repository.save_snapshot(snapshot)
    signal = BtcSqueezeSignalCalculator().calculate(snapshots)
    repository.save_signal(signal)
    unscored = _snapshot(as_of + timedelta(minutes=15), Decimal("106"), Decimal("100"))
    repository.save_snapshot(unscored)
    service = DerivativesObservationService(
        cast(Any, object()),
        repository,
        cast(Any, object()),
    )

    latest_snapshot, latest_signal = service.latest()

    assert latest_snapshot.snapshot_id == latest_signal.snapshot_id == signal.snapshot_id
    assert latest_snapshot != unscored


def test_market_stream_parsers_and_repository_aggregation(tmp_path) -> None:
    timestamp = 1_767_226_800_000
    liquidation = parse_binance_liquidation(
        """{"e":"forceOrder","E":1767226800000,"o":{"s":"BTCUSDT","S":"BUY","p":"100","ap":"101","l":"2","z":"3","T":1767226799000}}"""
    )
    coinbase = parse_coinbase_prices(
        """{"channel":"ticker_batch","timestamp":"2026-01-01T01:00:00Z","sequence_num":7,"events":[{"type":"update","tickers":[{"product_id":"BTC-USD","price":"102"}]}]}"""
    )

    assert liquidation is not None
    assert liquidation.position is LiquidatedPosition.SHORT
    assert liquidation.notional_usd == Decimal("202")
    assert len(coinbase) == 1
    assert coinbase[0].price_usd == Decimal("102")
    assert is_coinbase_heartbeat('{"channel":"heartbeats","events":[]}')
    assert message_is_stale(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 15, tzinfo=UTC),
        15,
    )

    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    repository.save_liquidation(liquidation)
    repository.save_liquidation(liquidation)
    repository.save_coinbase_price(coinbase[0])
    connected_at = datetime.fromtimestamp(timestamp / 1000, UTC) - timedelta(minutes=20)
    repository.mark_stream_connected(BINANCE_LIQUIDATION_STREAM, connected_at)

    assert repository.continuously_connected_since(
        BINANCE_LIQUIDATION_STREAM,
        datetime.fromtimestamp(timestamp / 1000, UTC) - timedelta(minutes=15),
    )
    assert repository.short_liquidation_notional(
        "BTCUSDT",
        datetime.fromtimestamp(timestamp / 1000, UTC) - timedelta(minutes=15),
        datetime.fromtimestamp(timestamp / 1000, UTC),
    ) == Decimal("202")
    assert repository.latest_coinbase_price(
        "BTC-USD", datetime(2026, 1, 1, 1, 1, tzinfo=UTC)
    ) == CoinbasePriceObservation(
        product_id="BTC-USD",
        observed_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        price_usd=Decimal("102"),
        source_sequence=7,
    )
    stale_at = datetime.fromtimestamp(timestamp / 1000, UTC)
    repository.mark_stream_stale(BINANCE_LIQUIDATION_STREAM, stale_at, "heartbeat timeout")
    status = repository.stream_statuses()[0]
    assert status.state is StreamState.STALE
    assert status.connected_since is None
    assert status.last_error == "heartbeat timeout"


def test_stream_ownership_lock_prevents_duplicate_collectors_and_allows_takeover(
    tmp_path,
) -> None:
    path = tmp_path / "derivatives.streams.lock"
    first = DirectoryStreamOwnershipLock(path)
    second = DirectoryStreamOwnershipLock(path)

    assert first.acquire()
    assert not second.acquire()

    first.release()
    assert second.acquire()
    second.release()


def test_stream_ownership_lock_reports_filesystem_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "derivatives.streams.lock"
    ownership = DirectoryStreamOwnershipLock(path)
    original_open = os.open

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise PermissionError("read-only lock directory")

    monkeypatch.setattr("investment.crypto.derivatives.streams.os.open", fail_open)
    with pytest.raises(PermissionError, match="read-only"):
        ownership.acquire()

    monkeypatch.setattr("investment.crypto.derivatives.streams.os.open", original_open)
    assert ownership.acquire()
    ownership.release()


def test_stream_ownership_lock_is_released_when_owner_process_dies(tmp_path) -> None:
    path = tmp_path / "derivatives.streams.lock"
    script = """
import sys
import time
from investment.crypto.derivatives.streams import DirectoryStreamOwnershipLock
lock = DirectoryStreamOwnershipLock(sys.argv[1])
assert lock.acquire()
print('locked', flush=True)
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        takeover = DirectoryStreamOwnershipLock(path, stale_after_seconds=0.05)
        assert not takeover.acquire()

        child.terminate()
        child.wait(timeout=5)
        _wait_until(takeover.acquire)
        takeover.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_market_stream_supervisor_is_idempotent_and_restarts_dead_workers(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    ownership = _FakeOwnershipLock()
    worker = _FakeRestartableWorker()
    streams = DerivativesMarketStreams(
        repository,
        ownership_lock=ownership,
        supervisor_interval_seconds=0.01,
    )
    streams._workers = (worker,)  # type: ignore[assignment]

    streams.start()
    streams.start()
    _wait_until(lambda: worker.starts == 1)
    assert ownership.acquire_calls == 1

    worker.alive = False
    _wait_until(lambda: worker.starts == 2)
    streams.stop()

    assert not streams.is_running
    assert ownership.release_calls == 1


def test_standby_supervisor_takes_over_after_active_collector_stops(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    lock_path = tmp_path / "shared.streams.lock"
    active_worker = _FakeRestartableWorker()
    standby_worker = _FakeRestartableWorker()
    active = DerivativesMarketStreams(
        repository,
        ownership_lock=DirectoryStreamOwnershipLock(lock_path),
        supervisor_interval_seconds=0.01,
    )
    standby = DerivativesMarketStreams(
        repository,
        ownership_lock=DirectoryStreamOwnershipLock(lock_path),
        supervisor_interval_seconds=0.01,
    )
    active._workers = (active_worker,)  # type: ignore[assignment]
    standby._workers = (standby_worker,)  # type: ignore[assignment]

    active.start()
    _wait_until(lambda: active_worker.starts == 1)
    standby.start()
    time.sleep(0.05)
    assert standby_worker.starts == 0

    active.stop()
    _wait_until(lambda: standby_worker.starts == 1)
    standby.stop()

    assert not active.is_running
    assert not standby.is_running


class _FakeOwnershipLock:
    def __init__(self) -> None:
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return True

    def release(self) -> None:
        self.release_calls += 1

    def refresh(self) -> None:
        pass


class _FakeRestartableWorker:
    def __init__(self) -> None:
        self.alive = False
        self.starts = 0

    def start(self) -> None:
        if self.alive:
            return
        self.alive = True
        self.starts += 1

    def join(self, timeout: float) -> None:
        del timeout
        self.alive = False

    def request_stop(self) -> None:
        self.alive = False

    @property
    def is_alive(self) -> bool:
        return self.alive


def _wait_until(predicate, timeout: float = 1) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        time.sleep(0.01)


def _snapshot(as_of: datetime, open_interest: Decimal, mark_price: Decimal) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        symbol="BTCUSDT",
        observed_at=as_of,
        available_at=as_of,
        mark_price=mark_price,
        index_price=mark_price,
        open_interest_usd=open_interest,
        funding_rate=Decimal("-0.0001"),
        basis_rate=Decimal("-0.001"),
        global_long_short_ratio=Decimal("0.8"),
        top_position_long_short_ratio=Decimal("0.85"),
        taker_buy_sell_ratio=Decimal("1.4"),
    )


def _spot_candles(as_of: datetime) -> tuple[MarketCandle, ...]:
    pair = TradingPair(Asset("BTC"), Asset("KRW", AssetKind.CASH))
    start = as_of - timedelta(minutes=15 * 17)
    candles: list[MarketCandle] = []
    for index in range(17):
        open_time = start + timedelta(minutes=15 * index)
        close = Decimal("100") if index < 16 else Decimal("102")
        volume = Decimal("100") if index < 16 else Decimal("300")
        candles.append(
            MarketCandle(
                pair,
                open_time,
                open_time + timedelta(minutes=15),
                close,
                close + 1,
                close - 1,
                close,
                volume,
            )
        )
    return tuple(candles)
