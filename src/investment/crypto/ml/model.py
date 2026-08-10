"""Deterministic sklearn model factories and evaluation models."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import polars as pl
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from investment.crypto.ml.dataset import FEATURE_COLUMNS, MLDataset
from investment.crypto.ml.split import PurgedWalkForwardConfig, PurgedWalkForwardSplitter


class ModelKind(StrEnum):
    RIDGE = "RIDGE"
    HIST_GRADIENT_BOOSTING = "HIST_GRADIENT_BOOSTING"


@dataclass(frozen=True, slots=True)
class PredictionMetrics:
    count: int
    rmse: float
    mae: float
    directional_hit_rate: float
    mean_daily_spearman_ic: float | None
    top_k_mean_forward_return: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FoldModelResult:
    fold: int
    model_kind: ModelKind
    validation: PredictionMetrics
    test: PredictionMetrics


@dataclass(frozen=True, slots=True)
class ModelComparisonResult:
    selected_model: ModelKind
    folds: tuple[FoldModelResult, ...]
    mean_validation_ic: dict[str, float | None]
    mean_test_ic: dict[str, float | None]


@dataclass(slots=True)
class TrainedReturnModel:
    kind: ModelKind
    estimator: Any
    features: tuple[str, ...]

    def predict(self, frame: pl.DataFrame) -> np.ndarray[Any, np.dtype[np.float64]]:
        values = frame.select(self.features).to_numpy()
        prediction = self.estimator.predict(values)
        return np.asarray(prediction, dtype=np.float64)


@dataclass(slots=True)
class TrainingResult:
    model: TrainedReturnModel
    comparison: ModelComparisonResult


class WalkForwardModelTrainer:
    def __init__(
        self,
        config: PurgedWalkForwardConfig,
        *,
        model_kinds: tuple[ModelKind, ...] = (
            ModelKind.RIDGE,
            ModelKind.HIST_GRADIENT_BOOSTING,
        ),
        top_k: int = 3,
    ) -> None:
        if not model_kinds or top_k <= 0:
            raise ValueError("model kinds and top_k are required")
        self.config = config
        self.model_kinds = model_kinds
        self.top_k = top_k

    def train(self, dataset: MLDataset) -> TrainingResult:
        if dataset.frame.is_empty():
            raise ValueError("cannot train on an empty ML dataset")
        if self.config.purge_days < dataset.metadata.label_horizon_days:
            raise ValueError("purge_days must cover the forward-label horizon")
        splits = PurgedWalkForwardSplitter(self.config).split(dataset.frame)
        if not splits:
            raise ValueError("dataset is too short for the walk-forward configuration")
        fold_results: list[FoldModelResult] = []
        for split in splits:
            for kind in self.model_kinds:
                estimator = _new_estimator(kind)
                estimator.fit(_x(split.train), _y(split.train))
                validation_prediction = np.asarray(
                    estimator.predict(_x(split.validation)), dtype=np.float64
                )
                test_prediction = np.asarray(
                    estimator.predict(_x(split.test)), dtype=np.float64
                )
                fold_results.append(
                    FoldModelResult(
                        split.fold,
                        kind,
                        _evaluate(split.validation, validation_prediction, self.top_k),
                        _evaluate(split.test, test_prediction, self.top_k),
                    )
                )
        validation_scores = {
            kind.value: _mean_ic(
                [
                    result.validation.mean_daily_spearman_ic
                    for result in fold_results
                    if result.model_kind is kind
                ]
            )
            for kind in self.model_kinds
        }
        test_scores = {
            kind.value: _mean_ic(
                [
                    result.test.mean_daily_spearman_ic
                    for result in fold_results
                    if result.model_kind is kind
                ]
            )
            for kind in self.model_kinds
        }
        def validation_score(kind: ModelKind) -> float:
            score = validation_scores[kind.value]
            return score if score is not None else float("-inf")

        selected = max(self.model_kinds, key=validation_score)
        final_estimator = _new_estimator(selected)
        final_estimator.fit(_x(dataset.frame), _y(dataset.frame))
        return TrainingResult(
            TrainedReturnModel(selected, final_estimator, FEATURE_COLUMNS),
            ModelComparisonResult(
                selected,
                tuple(fold_results),
                validation_scores,
                test_scores,
            ),
        )


def _new_estimator(kind: ModelKind) -> Pipeline:
    if kind is ModelKind.RIDGE:
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", Ridge(alpha=1.0)),
            ]
        )
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingRegressor(
                    learning_rate=0.05,
                    max_iter=100,
                    max_leaf_nodes=15,
                    l2_regularization=1.0,
                    random_state=42,
                ),
            ),
        ]
    )


def _x(frame: pl.DataFrame) -> np.ndarray[Any, Any]:
    return frame.select(FEATURE_COLUMNS).to_numpy()


def _y(frame: pl.DataFrame) -> np.ndarray[Any, Any]:
    return frame.get_column("forward_return").to_numpy()


def _evaluate(
    frame: pl.DataFrame, prediction: np.ndarray[Any, Any], top_k: int
) -> PredictionMetrics:
    actual = _y(frame).astype(np.float64)
    errors = prediction - actual
    scored = frame.select("as_of", "asset", "forward_return").with_columns(
        pl.Series("prediction", prediction)
    )
    daily_ics = []
    top_returns = []
    for group in scored.partition_by("as_of", maintain_order=True):
        if group.height >= 2:
            predicted = group.get_column("prediction").to_numpy()
            realized = group.get_column("forward_return").to_numpy()
            if np.std(predicted) > 0 and np.std(realized) > 0:
                value = float(stats.spearmanr(predicted, realized).statistic)
                if np.isfinite(value):
                    daily_ics.append(value)
        top = group.sort("prediction", descending=True).head(top_k)
        mean = top.get_column("forward_return").mean()
        if isinstance(mean, (int, float)):
            top_returns.append(float(mean))
    return PredictionMetrics(
        count=len(actual),
        rmse=float(np.sqrt(np.mean(errors**2))),
        mae=float(np.mean(np.abs(errors))),
        directional_hit_rate=float(np.mean((prediction > 0) == (actual > 0))),
        mean_daily_spearman_ic=float(np.mean(daily_ics)) if daily_ics else None,
        top_k_mean_forward_return=float(np.mean(top_returns)) if top_returns else 0.0,
    )


def _mean_ic(values: list[float | None]) -> float | None:
    finite = [value for value in values if value is not None and np.isfinite(value)]
    return float(np.mean(finite)) if finite else None
