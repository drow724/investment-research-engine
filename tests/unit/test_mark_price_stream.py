import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from investment.crypto.derivatives.binance import BinanceIntradayDerivativesClient
from investment.crypto.derivatives.mark_price import parse_mark_price
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.derivatives.streams import DerivativesMarketStreams

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def message(age=1, **overrides):
    return json.dumps(
        {
            "e": "markPriceUpdate",
            "s": "BTCUSDT",
            "E": int((NOW - timedelta(seconds=age)).timestamp() * 1000),
            "p": "100100",
            "i": "100000",
            "r": "-0.0001",
            "T": int((NOW + timedelta(hours=8)).timestamp() * 1000),
            **overrides,
        }
    )


def test_persistence_and_point_in_time_lookup(tmp_path):
    repo = SqliteDerivativesObservationRepository(tmp_path / "test.sqlite3")
    streams = DerivativesMarketStreams(repo, clock=lambda: NOW, mark_price_url="ws://test")
    assert len(streams._workers) == 3
    assert streams._workers[-1].url == ("ws://test")
    assert streams._handle_mark_price(message())
    saved = repo.mark_price_known_at("BTCUSDT", NOW)
    assert saved is not None
    assert saved.funding_rate == Decimal("-0.0001")
    assert repo.mark_price_known_at("BTCUSDT", NOW - timedelta(microseconds=1)) is None
    older = parse_mark_price(message(age=10), NOW)
    repo.save_mark_price(older)
    assert repo.mark_price_known_at("BTCUSDT", NOW) == saved
    duplicate = parse_mark_price(message(p="99999"), NOW + timedelta(seconds=1))
    repo.save_mark_price(duplicate)
    assert repo.mark_price_known_at("BTCUSDT", NOW + timedelta(seconds=1)) == saved


@pytest.mark.parametrize("fields", [{"p": "NaN"}, {"i": "0"}, {"r": "Infinity"}])
def test_invalid_prices_are_rejected(fields):
    with pytest.raises(ValueError):
        parse_mark_price(message(**fields), NOW)


def test_small_source_clock_skew_is_available_at_source_time():
    observation = parse_mark_price(message(age=-1), NOW)

    assert observation is not None
    assert observation.received_at == observation.event_at


def test_large_source_clock_skew_is_rejected():
    with pytest.raises(ValueError, match="too far ahead"):
        parse_mark_price(message(age=-3), NOW)


def test_transport_or_incomplete_messages_are_ignored():
    assert parse_mark_price("not json", NOW) is None
    assert parse_mark_price('{"e": "markPriceUpdate", "s": "BTCUSDT"}', NOW) is None


@pytest.mark.parametrize("age,rest_expected", [(1, False), (15, False), (16, True)])
def test_snapshot_uses_fresh_stream_and_never_official_basis(age, rest_expected):
    paths = []

    def handler(request):
        path = request.url.path
        paths.append(path)
        timestamp = int(NOW.timestamp() * 1000)
        if path.endswith("premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "100200",
                    "indexPrice": "100000",
                    "lastFundingRate": "0.0002",
                    "time": timestamp,
                },
            )
        if path.endswith("openInterest"):
            return httpx.Response(200, json={"openInterest": "100", "time": timestamp})
        assert not path.endswith("basis")
        return httpx.Response(
            200,
            json=[
                {
                    "timestamp": timestamp,
                    "longShortRatio": "1",
                    "buySellRatio": "1",
                }
            ],
        )

    observation = parse_mark_price(message(age=age), NOW)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = BinanceIntradayDerivativesClient(
            client=client,
            official_basis_enabled=False,
            mark_price_provider=lambda symbol, at: observation,
        ).fetch_snapshot(NOW)
    assert ("/fapi/v1/premiumIndex" in paths) == rest_expected
    assert len(paths) == (5 if rest_expected else 4)
    assert snapshot.mark_price == Decimal("100200" if rest_expected else "100100")
    assert snapshot.funding_rate == Decimal("0.0002" if rest_expected else "-0.0001")
    assert snapshot.basis_rate is None
    assert "basis_rate" in snapshot.missing_fields
