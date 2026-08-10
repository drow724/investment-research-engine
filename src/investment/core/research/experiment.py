"""Experiment execution and filesystem registry."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from investment.core.domain.experiment import DatasetSnapshot, ExperimentConfig
from investment.core.research.evaluator import FeatureEvaluator
from investment.core.research.stability import FeatureStabilityAnalyzer


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    experiment_id: str
    status: str
    config: ExperimentConfig
    dataset_snapshot: DatasetSnapshot
    feature_versions: dict[str, str]
    results: dict[str, dict[str, object]]
    stability: dict[str, dict[str, object]]
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ExperimentRunner:
    def run(
        self,
        frame: pl.DataFrame,
        config: ExperimentConfig,
        snapshot: DatasetSnapshot,
        feature_versions: dict[str, str],
        *,
        created_at: datetime | None = None,
    ) -> ExperimentRun:
        required = {*config.features, *config.labels, "open_time"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing experiment columns: {sorted(missing)}")
        sample = frame.filter(
            (pl.col("open_time") >= config.start_time)
            & (pl.col("open_time") < config.end_time)
        )
        results: dict[str, dict[str, object]] = {}
        stability: dict[str, dict[str, object]] = {}
        quantiles_value = config.parameters.get("quantiles", 5)
        if not isinstance(quantiles_value, int):
            raise ValueError("quantiles parameter must be an integer")
        for feature in config.features:
            for label in config.labels:
                key = label if len(config.features) == 1 else f"{feature}::{label}"
                mdd = label.replace("forward_return_", "forward_mdd_")
                result = FeatureEvaluator().evaluate(
                    sample,
                    feature,
                    label,
                    quantiles=quantiles_value,
                    mdd_label=mdd if mdd in sample.columns else None,
                )
                results[key] = result.to_dict()
                stability[key] = FeatureStabilityAnalyzer().by_year(
                    sample, feature, label
                ).to_dict()
        identity = hashlib.sha256(
            f"{snapshot.snapshot_id}:{config.canonical_json()}".encode()
        ).hexdigest()[:16]
        return ExperimentRun(
            experiment_id=f"exp-{identity}",
            status="COMPLETED",
            config=config,
            dataset_snapshot=snapshot,
            feature_versions=feature_versions,
            results=results,
            stability=stability,
            created_at=created_at or datetime.now(UTC),
        )


class ExperimentRegistry:
    def __init__(self, root: str | Path = "experiments/output") -> None:
        self.root = Path(root)

    def save(self, run: ExperimentRun) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{run.experiment_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(run.to_dict(), default=_json_default, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def load(self, experiment_id: str) -> dict[str, object]:
        path = self.root / f"{experiment_id}.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid experiment registry entry")
        return loaded


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")
