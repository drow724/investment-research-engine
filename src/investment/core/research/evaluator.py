"""Feature predictive evaluation without trading-strategy assumptions."""

from dataclasses import asdict, dataclass
from typing import cast

import numpy as np
import polars as pl
from scipy import stats


@dataclass(frozen=True, slots=True)
class QuantileMetrics:
    quantile: int
    count: int
    mean_forward_return: float
    median_forward_return: float
    hit_rate: float
    mean_forward_mdd: float | None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    feature: str
    label: str
    count: int
    pearson_ic: float | None
    spearman_rank_ic: float | None
    quantiles: tuple[QuantileMetrics, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FeatureEvaluator:
    def evaluate(
        self,
        dataset: pl.DataFrame,
        feature: str,
        label: str,
        quantiles: int = 5,
        mdd_label: str | None = None,
    ) -> EvaluationResult:
        if quantiles < 2:
            raise ValueError("quantiles must be at least two")
        required = {feature, label}
        missing = required.difference(dataset.columns)
        if missing:
            raise ValueError(f"missing evaluation columns: {sorted(missing)}")
        selected = [feature, label, *([mdd_label] if mdd_label else [])]
        clean = dataset.select(selected).drop_nulls(subset=[feature, label])
        if clean.height < quantiles:
            raise ValueError("not enough complete observations for requested quantiles")

        x = clean.get_column(feature).to_numpy()
        y = clean.get_column(label).to_numpy()
        pearson = _finite_correlation(stats.pearsonr(x, y).statistic)
        spearman = _finite_correlation(stats.spearmanr(x, y).statistic)

        # Ranking makes duplicate values deterministic while keeping all bins useful.
        ranks = stats.rankdata(x, method="ordinal")
        bins = np.minimum(((ranks - 1) * quantiles // len(ranks)) + 1, quantiles)
        working = clean.with_columns(pl.Series("_quantile", bins.astype(np.int32)))
        summaries: list[QuantileMetrics] = []
        for number in range(1, quantiles + 1):
            group = working.filter(pl.col("_quantile") == number)
            returns = group.get_column(label)
            mdd = None
            if mdd_label and mdd_label in group.columns:
                mdd_value = group.get_column(mdd_label).mean()
                mdd = float(cast(float, mdd_value)) if mdd_value is not None else None
            mean_return = cast(float, returns.mean())
            median_return = cast(float, returns.median())
            hit_rate = cast(float, (returns > 0).mean())
            summaries.append(
                QuantileMetrics(
                    quantile=number,
                    count=group.height,
                    mean_forward_return=float(mean_return),
                    median_forward_return=float(median_return),
                    hit_rate=float(hit_rate),
                    mean_forward_mdd=mdd,
                )
            )
        return EvaluationResult(
            feature=feature,
            label=label,
            count=clean.height,
            pearson_ic=pearson,
            spearman_rank_ic=spearman,
            quantiles=tuple(summaries),
        )


def _finite_correlation(value: object) -> float | None:
    numeric = float(cast(float, value))
    return numeric if np.isfinite(numeric) else None
