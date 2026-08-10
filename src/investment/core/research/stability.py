"""Deterministic sub-period feature stability analysis."""

from dataclasses import asdict, dataclass

import polars as pl

from investment.core.research.evaluator import FeatureEvaluator


@dataclass(frozen=True, slots=True)
class StabilityPeriodResult:
    period: str
    count: int
    pearson_ic: float | None
    spearman_rank_ic: float | None
    mean_forward_return: float


@dataclass(frozen=True, slots=True)
class FeatureStabilityResult:
    feature: str
    label: str
    grouping: str
    periods: tuple[StabilityPeriodResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FeatureStabilityAnalyzer:
    def by_year(
        self, frame: pl.DataFrame, feature: str, label: str
    ) -> FeatureStabilityResult:
        years = frame.get_column("open_time").dt.year().unique().sort().to_list()
        groups = [
            (str(year), frame.filter(pl.col("open_time").dt.year() == year)) for year in years
        ]
        return self._evaluate_groups(groups, feature, label, "YEAR")

    def rolling_years(
        self, frame: pl.DataFrame, feature: str, label: str, window_years: int = 2
    ) -> FeatureStabilityResult:
        if window_years <= 0:
            raise ValueError("window_years must be positive")
        years = frame.get_column("open_time").dt.year().unique().sort().to_list()
        groups: list[tuple[str, pl.DataFrame]] = []
        for index in range(len(years) - window_years + 1):
            selected = years[index : index + window_years]
            groups.append(
                (
                    f"{selected[0]}-{selected[-1]}",
                    frame.filter(pl.col("open_time").dt.year().is_in(selected)),
                )
            )
        return self._evaluate_groups(groups, feature, label, f"ROLLING_{window_years}Y")

    def by_market_structure(
        self, frame: pl.DataFrame, feature: str, label: str
    ) -> FeatureStabilityResult:
        if "price_vs_ma_200" not in frame.columns:
            raise ValueError("price_vs_ma_200 is required for bull/bear analysis")
        groups = [
            ("BULL_DETERMINISTIC", frame.filter(pl.col("price_vs_ma_200") >= 0)),
            ("BEAR_DETERMINISTIC", frame.filter(pl.col("price_vs_ma_200") < 0)),
        ]
        return self._evaluate_groups(groups, feature, label, "MARKET_STRUCTURE")

    def by_volatility(
        self, frame: pl.DataFrame, feature: str, label: str
    ) -> FeatureStabilityResult:
        if "realized_vol_30d" not in frame.columns:
            raise ValueError("realized_vol_30d is required for volatility analysis")
        median = frame.get_column("realized_vol_30d").median()
        if not isinstance(median, (int, float)):
            raise ValueError("realized volatility has no finite median")
        groups = [
            ("HIGH_VOLATILITY", frame.filter(pl.col("realized_vol_30d") >= median)),
            ("LOW_VOLATILITY", frame.filter(pl.col("realized_vol_30d") < median)),
        ]
        return self._evaluate_groups(groups, feature, label, "VOLATILITY")

    def _evaluate_groups(
        self,
        groups: list[tuple[str, pl.DataFrame]],
        feature: str,
        label: str,
        grouping: str,
    ) -> FeatureStabilityResult:
        periods: list[StabilityPeriodResult] = []
        for name, group in groups:
            clean = group.select(feature, label).drop_nulls()
            if clean.height < 2:
                continue
            result = FeatureEvaluator().evaluate(clean, feature, label, quantiles=2)
            mean = clean.get_column(label).mean()
            if not isinstance(mean, (int, float)):
                continue
            periods.append(
                StabilityPeriodResult(
                    name,
                    result.count,
                    result.pearson_ic,
                    result.spearman_rank_ic,
                    float(mean),
                )
            )
        return FeatureStabilityResult(feature, label, grouping, tuple(periods))
