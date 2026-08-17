from datetime import UTC, datetime

from investment.bitcoin.data.price.normalizer import BinanceDailyNormalizer
from investment.bitcoin.data.price.schema import OHLCV_COLUMNS
from investment.core.data.provider import RawMarketData


def test_binance_payload_has_stable_normalized_schema() -> None:
    raw = RawMarketData(
        symbol="BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 2, tzinfo=UTC),
        source="binance",
        records=[[1704067200000, "100", "110", "90", "105", "12.5", 1704153599999, "0", 1]],
    )
    result = BinanceDailyNormalizer().normalize(raw, ingested_at=datetime(2024, 1, 3, tzinfo=UTC))
    assert result.columns == OHLCV_COLUMNS
    assert result.row(0, named=True)["close"] == 105.0
    assert result.row(0, named=True)["available_at"].tzinfo is not None
