"""Supported candle timeframes for daily and personal intraday research."""

from enum import StrEnum


class CandleTimeframe(StrEnum):
    MINUTE_15 = "15m"
    HOUR_1 = "60m"
    HOUR_4 = "240m"
    DAY_1 = "1d"

    @property
    def minutes(self) -> int:
        return {
            CandleTimeframe.MINUTE_15: 15,
            CandleTimeframe.HOUR_1: 60,
            CandleTimeframe.HOUR_4: 240,
            CandleTimeframe.DAY_1: 1440,
        }[self]

    @property
    def upbit_unit(self) -> int:
        if self is CandleTimeframe.DAY_1:
            raise ValueError("daily candles do not use an Upbit minute unit")
        return self.minutes
