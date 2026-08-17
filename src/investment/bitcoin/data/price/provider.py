"""Deprecated compatibility import; market-data ownership moved out of Bitcoin research."""

from investment.market_data.crypto.binance import (
    BinanceBitcoinPriceProvider,
    utc_datetime,
)

__all__ = ["BinanceBitcoinPriceProvider", "utc_datetime"]
