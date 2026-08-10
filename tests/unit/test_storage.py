from datetime import UTC, datetime

import polars as pl

from investment.bitcoin.data.price.normalizer import BinanceDailyNormalizer
from investment.core.data.provider import RawMarketData
from investment.core.data.storage import NormalizedParquetStorage, RawJsonStorage


def test_storage_paths_are_deterministic_and_normalized_save_is_idempotent(tmp_path) -> None:
    raw = RawMarketData(
        "BTCUSDT",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        "binance",
        [[1704067200000, "100", "110", "90", "105", "12.5", 1704153599999]],
    )
    raw_store = RawJsonStorage(tmp_path / "raw")
    assert raw_store.save(raw) == raw_store.save(raw)

    normalized = BinanceDailyNormalizer().normalize(raw)
    store = NormalizedParquetStorage(tmp_path / "normalized")
    store.save(raw.symbol, normalized)
    store.save(raw.symbol, normalized)
    assert store.read(raw.symbol).height == 1
    assert isinstance(store.read(raw.symbol), pl.DataFrame)
