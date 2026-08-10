from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class PressurePriceDivergenceFeature:
    pressure_column: str
    horizon_days: int

    @property
    def name(self) -> str:
        prefix = self.pressure_column.removesuffix("_pressure")
        return f"{prefix}_price_divergence_{self.horizon_days}d"

    def compute(self, frame: pl.DataFrame) -> pl.DataFrame:
        required = {self.pressure_column, "close"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing divergence inputs: {sorted(missing)}")
        price_return = pl.col("close") / pl.col("close").shift(self.horizon_days) - 1
        pressure_change = pl.col(self.pressure_column) - pl.col(self.pressure_column).shift(
            self.horizon_days
        )
        return frame.with_columns((pressure_change - price_return).alias(self.name))
