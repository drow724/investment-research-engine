"""Application services for leakage-safe crypto model training and inference."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.ml.dataset import CrossSectionalDatasetBuilder, UniverseMode
from investment.crypto.ml.model import ModelKind, WalkForwardModelTrainer
from investment.crypto.ml.registry import (
    CryptoModelRegistry,
    ModelActivationRecord,
    ModelArtifactMetadata,
)
from investment.crypto.ml.split import PurgedWalkForwardConfig
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.ports.universe import UniverseEligibilityModel


@dataclass(frozen=True, slots=True)
class TrainModelCommand:
    pair_symbols: tuple[str, ...]
    start: datetime
    end: datetime
    universe_mode: UniverseMode = UniverseMode.POINT_IN_TIME
    label_horizon_days: int = 7
    model_kinds: tuple[ModelKind, ...] = (
        ModelKind.RIDGE,
        ModelKind.HIST_GRADIENT_BOOSTING,
    )
    split: PurgedWalkForwardConfig = PurgedWalkForwardConfig()
    top_k: int = 3


@dataclass(frozen=True, slots=True)
class TrainModelResult:
    metadata: ModelArtifactMetadata
    rows: int


@dataclass(frozen=True, slots=True)
class PredictReturnsCommand:
    pair_symbols: tuple[str, ...]
    as_of: datetime
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class AssetPrediction:
    asset: str
    expected_return: float


@dataclass(frozen=True, slots=True)
class PredictReturnsResult:
    model_id: str
    as_of: datetime
    horizon_days: int
    universe_mode: UniverseMode
    predictions: tuple[AssetPrediction, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivateModelCommand:
    model_id: str
    approved_by: str


@dataclass(frozen=True, slots=True)
class ModelActivationPolicy:
    version: str = "model-activation-v1"
    minimum_validation_ic: float = 0.0
    minimum_test_ic: float = 0.0


@dataclass(frozen=True, slots=True)
class ActivateModelResult:
    metadata: ModelArtifactMetadata
    activation: ModelActivationRecord


class CryptoMLService:
    def __init__(
        self,
        provider: CryptoMarketDataProvider,
        registry: CryptoModelRegistry,
        eligibility: UniverseEligibilityModel | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._eligibility = eligibility

    def train(self, command: TrainModelCommand) -> TrainModelResult:
        builder = self._builder(command.universe_mode)
        universe = build_universe(command.pair_symbols)
        warmup = timedelta(days=builder.maximum_feature_lookback * 2)
        future = timedelta(days=command.label_horizon_days * 2 + 2)
        market_data = self._provider.fetch(universe, command.start - warmup, command.end + future)
        dataset = builder.build(
            market_data,
            command.start,
            command.end,
            label_horizon_days=command.label_horizon_days,
        )
        trained = WalkForwardModelTrainer(
            command.split, model_kinds=command.model_kinds, top_k=command.top_k
        ).train(dataset)
        metadata = self._registry.save(trained.model, dataset.metadata, trained.comparison)
        return TrainModelResult(metadata, dataset.frame.height)

    def predict(self, command: PredictReturnsCommand) -> PredictReturnsResult:
        model, metadata = (
            self._registry.load(command.model_id)
            if command.model_id is not None
            else self._registry.load_active()
        )
        mode = UniverseMode(metadata.universe_mode)
        builder = self._builder(mode)
        if metadata.feature_version != builder.feature_version:
            raise ValueError("registered model uses an unsupported feature version")
        universe = build_universe(command.pair_symbols)
        warmup = timedelta(days=builder.maximum_feature_lookback * 2)
        market_data = self._provider.fetch(
            universe, command.as_of - warmup, command.as_of + timedelta(days=1)
        )
        features = builder.build_current_features(market_data, command.as_of)
        if features.is_empty():
            raise ValueError("no eligible assets have enough point-in-time history")
        if tuple(model.features) != tuple(metadata.features):
            raise ValueError("registered model feature metadata does not match artifact")
        scored = (
            features.select("asset")
            .with_columns(pl.Series("expected_return", model.predict(features)))
            .sort("expected_return", descending=True)
        )
        predictions = tuple(
            AssetPrediction(str(row["asset"]), float(row["expected_return"]))
            for row in scored.iter_rows(named=True)
        )
        return PredictReturnsResult(
            metadata.model_id,
            command.as_of,
            metadata.label_horizon_days,
            mode,
            predictions,
            metadata.limitations,
        )

    def latest_model(self) -> ModelArtifactMetadata:
        return self._registry.load_latest()[1]

    def active_model(self) -> ModelArtifactMetadata:
        return self._registry.active_metadata()

    def activate(
        self,
        command: ActivateModelCommand,
        policy: ModelActivationPolicy | None = None,
    ) -> ActivateModelResult:
        selected_policy = policy or ModelActivationPolicy()
        _, metadata = self._registry.load(command.model_id)
        selected = str(metadata.comparison.get("selected_model", ""))
        validation_ic = _selected_score(metadata, "mean_validation_ic", selected)
        test_ic = _selected_score(metadata, "mean_test_ic", selected)
        violations = []
        if validation_ic <= selected_policy.minimum_validation_ic:
            violations.append("NON_POSITIVE_VALIDATION_IC")
        if test_ic <= selected_policy.minimum_test_ic:
            violations.append("NON_POSITIVE_TEST_IC")
        if violations:
            raise ValueError(f"model activation policy rejected candidate: {violations}")
        activation = self._registry.activate(
            metadata.model_id,
            approved_by=command.approved_by,
            policy_version=selected_policy.version,
            validation_ic=validation_ic,
            test_ic=test_ic,
        )
        return ActivateModelResult(metadata, activation)

    def _builder(self, mode: UniverseMode) -> CrossSectionalDatasetBuilder:
        if mode is UniverseMode.POINT_IN_TIME and self._eligibility is None:
            raise ValueError("POINT_IN_TIME mode requires collected universe snapshots")
        return CrossSectionalDatasetBuilder(mode, self._eligibility)


def _selected_score(metadata: ModelArtifactMetadata, key: str, selected: str) -> float:
    values = metadata.comparison.get(key)
    if not isinstance(values, dict) or selected not in values:
        raise ValueError(f"model comparison is missing {key} for selected model")
    value = values[selected]
    if not isinstance(value, (int, float)):
        raise ValueError(f"model comparison has invalid {key}")
    return float(value)
