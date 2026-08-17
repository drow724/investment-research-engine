from datetime import UTC, datetime

import httpx

from investment.bitcoin.data.derivatives.provider import BinanceDerivativesProvider
from investment.bitcoin.data.etf.provider import HttpBitcoinEtfFlowProvider


def test_etf_http_provider_keeps_vendor_records_raw() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"].endswith("+00:00")
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "event_time": "2024-01-01T00:00:00Z",
                        "published_at": "2024-01-02T00:00:00Z",
                        "fund": "IBIT",
                        "value": 10,
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = HttpBitcoinEtfFlowProvider(
            "https://vendor.test/flows", "vendor", client=client
        ).fetch(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC))
    assert result.dataset == "etf"
    assert result.records[0]["fund"] == "IBIT"


def test_binance_derivatives_provider_maps_supported_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("fundingRate"):
            return httpx.Response(
                200,
                json=[{"fundingTime": 1_704_067_200_000, "fundingRate": "0.001"}],
            )
        return httpx.Response(
            200,
            json=[{"timestamp": 1_704_067_200_000, "sumOpenInterestValue": "1000000"}],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BinanceDerivativesProvider(client=client).fetch(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
    assert {record["metric"] for record in result.records} == {
        "funding_rate",
        "open_interest",
    }
