from datetime import UTC, datetime

import httpx

from investment.bitcoin.data.price.provider import BinanceBitcoinPriceProvider


def test_provider_sends_daily_half_open_request_and_returns_raw_records() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                [1704067200000, "100", "110", "90", "105", "12.5", 1704153599999]
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BinanceBitcoinPriceProvider(
            base_url="https://binance.test", client=client
        ).fetch(
            "BTCUSDT",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

    assert len(result.records) == 1
    assert result.source == "binance"
    assert captured[0].url.params["interval"] == "1d"
    assert captured[0].url.params["endTime"] == "1704153599999"
