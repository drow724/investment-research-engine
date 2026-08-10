"""Atomic local-JSON research lifecycle repository."""

import json
from dataclasses import asdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from investment.crypto.backtest.models import PerformanceMetrics
from investment.crypto.domain.research import (
    BacktestRun,
    CandidateEvaluation,
    DatasetSnapshot,
    Experiment,
    ExperimentStatus,
    ExperimentType,
    ModelVersion,
    ResearchPeriod,
    RunStatus,
    StrategyVersion,
    TrainingRun,
    ValidationMethod,
    ValidationRun,
    VersionStatus,
)


class JsonResearchLifecycleRepository:
    def __init__(self, root: str | Path = "experiments/lifecycle") -> None:
        self.root = Path(root)

    def save_experiment(self, experiment: Experiment, *, create: bool = False) -> None:
        self._write("experiments", experiment.experiment_id, asdict(experiment), create)

    def get_experiment(self, experiment_id: str) -> Experiment:
        return _experiment(self._read("experiments", experiment_id))

    def list_experiments(self) -> tuple[Experiment, ...]:
        return tuple(_experiment(value) for value in self._read_all("experiments"))

    def save_strategy(self, version: StrategyVersion, *, create: bool = False) -> None:
        self._write("strategies", version.version_id, asdict(version), create)

    def get_strategy(self, version_id: str) -> StrategyVersion:
        return _strategy(self._read("strategies", version_id))

    def list_strategies(self) -> tuple[StrategyVersion, ...]:
        return tuple(_strategy(value) for value in self._read_all("strategies"))

    def active_strategy(self, name: str) -> StrategyVersion:
        active = [
            item
            for item in self.list_strategies()
            if item.strategy_name == name and item.status is VersionStatus.ACTIVE
        ]
        if len(active) != 1:
            raise FileNotFoundError(f"active strategy not found: {name}")
        return active[0]

    def save_model(self, version: ModelVersion, *, create: bool = False) -> None:
        self._write("models", version.version_id, asdict(version), create)

    def list_models(self) -> tuple[ModelVersion, ...]:
        return tuple(_model(value) for value in self._read_all("models"))

    def save_snapshot(self, snapshot: DatasetSnapshot) -> None:
        self._write("snapshots", snapshot.snapshot_id, asdict(snapshot), False)

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        value = self._read("snapshots", snapshot_id)
        return DatasetSnapshot(
            str(value["snapshot_id"]),
            _datetime(value["data_start"]),
            _datetime(value["data_end"]),
            str(value["universe_version"]),
            str(value["feature_version"]),
            str(value["source"]),
            str(value["checksum"]),
            _datetime(value["created_at"]),
        )

    def save_backtest_run(self, run: BacktestRun, *, create: bool = False) -> None:
        self._write("backtest-runs", run.run_id, asdict(run), create)

    def save_training_run(self, run: TrainingRun, *, create: bool = False) -> None:
        self._write("training-runs", run.run_id, asdict(run), create)

    def get_training_run(self, run_id: str) -> TrainingRun:
        value = self._read("training-runs", run_id)
        return TrainingRun(
            str(value["run_id"]),
            str(value["experiment_id"]),
            str(value["dataset_snapshot_id"]),
            str(value["model_version_id"]),
            RunStatus(value["status"]),
            _datetime(value["started_at"]),
            _optional_datetime(value.get("completed_at")),
            str(value["metrics_json"]),
            _optional_str(value.get("error")),
        )

    def get_backtest_run(self, run_id: str) -> BacktestRun:
        return _backtest_run(self._read("backtest-runs", run_id))

    def save_validation_run(self, run: ValidationRun, *, create: bool = False) -> None:
        self._write("validation-runs", run.run_id, asdict(run), create)

    def _write(
        self, collection: str, identity: str, payload: dict[str, object], create: bool
    ) -> None:
        directory = self.root / collection
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{identity}.json"
        if create and path.exists():
            raise FileExistsError(f"research record already exists: {identity}")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, default=_json_default, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read(self, collection: str, identity: str) -> dict[str, Any]:
        value = json.loads(
            (self.root / collection / f"{identity}.json").read_text(encoding="utf-8")
        )
        if not isinstance(value, dict):
            raise ValueError("invalid lifecycle record")
        return value

    def _read_all(self, collection: str) -> tuple[dict[str, Any], ...]:
        directory = self.root / collection
        if not directory.exists():
            return ()
        return tuple(self._read(collection, path.stem) for path in sorted(directory.glob("*.json")))


def _strategy(value: dict[str, Any]) -> StrategyVersion:
    return StrategyVersion(
        str(value["version_id"]),
        str(value["strategy_name"]),
        int(value["version"]),
        str(value["parameters_json"]),
        VersionStatus(value["status"]),
        _datetime(value["created_at"]),
        _optional_str(value.get("source_experiment_id")),
    )


def _model(value: dict[str, Any]) -> ModelVersion:
    return ModelVersion(
        str(value["version_id"]),
        str(value["model_name"]),
        int(value["version"]),
        str(value["artifact_id"]),
        VersionStatus(value["status"]),
        _datetime(value["created_at"]),
        str(value["source_experiment_id"]),
    )


def _experiment(value: dict[str, Any]) -> Experiment:
    evaluation_value = value.get("evaluation")
    evaluation = (
        CandidateEvaluation(
            bool(evaluation_value["passed"]),
            str(evaluation_value["policy_version"]),
            tuple(evaluation_value["reasons"]),
        )
        if isinstance(evaluation_value, dict)
        else None
    )
    return Experiment(
        str(value["experiment_id"]),
        str(value["hypothesis"]),
        ExperimentType(value["experiment_type"]),
        _optional_str(value.get("base_strategy_version")),
        _optional_str(value.get("base_model_version")),
        _optional_str(value.get("candidate_strategy_version")),
        _optional_str(value.get("candidate_model_version")),
        str(value["parameters_json"]),
        tuple(value["feature_changes"]),
        _period(value["train_period"]),
        _period(value["validation_period"]),
        ValidationMethod(value["validation_method"]),
        tuple(value["requested_metrics"]),
        ExperimentStatus(value["status"]),
        _datetime(value["created_at"]),
        _optional_datetime(value.get("started_at")),
        _optional_datetime(value.get("completed_at")),
        _optional_str(value.get("dataset_snapshot_id")),
        tuple(value["run_ids"]),
        evaluation,
        _optional_str(value.get("approved_by")),
    )


def _backtest_run(value: dict[str, Any]) -> BacktestRun:
    metrics_value = value.get("metrics")
    metrics = PerformanceMetrics(**metrics_value) if isinstance(metrics_value, dict) else None
    return BacktestRun(
        str(value["run_id"]),
        str(value["experiment_id"]),
        str(value["strategy_version_id"]),
        str(value["dataset_snapshot_id"]),
        str(value["fee_rate"]),
        str(value["slippage_rate"]),
        RunStatus(value["status"]),
        _datetime(value["started_at"]),
        _optional_datetime(value.get("completed_at")),
        metrics,
        _optional_str(value.get("error")),
    )


def _period(value: dict[str, Any]) -> ResearchPeriod:
    return ResearchPeriod(_datetime(value["start"]), _datetime(value["end"]))


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(type(value).__name__)
