"""Local versioned model artifact registry."""

import hashlib
import json
import pickle
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from investment.crypto.ml.dataset import MLDatasetMetadata
from investment.crypto.ml.model import (
    ModelComparisonResult,
    ModelKind,
    TrainedReturnModel,
)


@dataclass(frozen=True, slots=True)
class ModelArtifactMetadata:
    model_id: str
    model_kind: ModelKind
    feature_version: str
    features: tuple[str, ...]
    label_horizon_days: int
    universe_mode: str
    dataset_hash: str
    trained_start: datetime
    trained_end: datetime
    created_at: datetime
    comparison: dict[str, object]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelActivationRecord:
    model_id: str
    approved_by: str
    activated_at: datetime
    previous_model_id: str | None
    policy_version: str
    validation_ic: float
    test_ic: float


class CryptoModelRegistry:
    def __init__(self, root: str | Path = "models/crypto") -> None:
        self.root = Path(root)

    def save(
        self,
        model: TrainedReturnModel,
        dataset: MLDatasetMetadata,
        comparison: ModelComparisonResult,
        *,
        created_at: datetime | None = None,
    ) -> ModelArtifactMetadata:
        created = created_at or datetime.now(UTC)
        identity = json.dumps(
            {
                "kind": model.kind.value,
                "features": model.features,
                "horizon": dataset.label_horizon_days,
                "universe_mode": dataset.universe_mode.value,
                "dataset_hash": dataset.content_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        model_id = f"crypto-model-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        directory = self.root / model_id
        directory.mkdir(parents=True, exist_ok=True)
        metadata = ModelArtifactMetadata(
            model_id,
            model.kind,
            dataset.feature_version,
            model.features,
            dataset.label_horizon_days,
            dataset.universe_mode.value,
            dataset.content_hash,
            dataset.start,
            dataset.end,
            created,
            _comparison_dict(comparison),
            dataset.limitations,
        )
        model_path = directory / "model.pkl"
        temporary_model = model_path.with_suffix(".pkl.tmp")
        with temporary_model.open("wb") as file:
            pickle.dump(model, file)
        temporary_model.replace(model_path)
        metadata_path = directory / "metadata.json"
        temporary_metadata = metadata_path.with_suffix(".json.tmp")
        temporary_metadata.write_text(
            json.dumps(asdict(metadata), default=_json_default, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary_metadata.replace(metadata_path)
        (self.root / "LATEST").write_text(model_id, encoding="utf-8")
        return metadata

    def activate(
        self,
        model_id: str,
        *,
        approved_by: str,
        policy_version: str,
        validation_ic: float,
        test_ic: float,
        activated_at: datetime | None = None,
    ) -> ModelActivationRecord:
        """Human-approved boundary; training itself never changes the active model."""
        if not approved_by.strip():
            raise ValueError("approved_by is required for model activation")
        self.load(model_id)
        self.root.mkdir(parents=True, exist_ok=True)
        active_path = self.root / "ACTIVE"
        previous = active_path.read_text(encoding="utf-8").strip() if active_path.exists() else None
        record = ModelActivationRecord(
            model_id,
            approved_by.strip(),
            activated_at or datetime.now(UTC),
            previous or None,
            policy_version,
            validation_ic,
            test_ic,
        )
        audit_directory = self.root / "activations"
        audit_directory.mkdir(parents=True, exist_ok=True)
        audit_path = audit_directory / f"{record.activated_at.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
        temporary_audit = audit_path.with_suffix(".json.tmp")
        temporary_audit.write_text(
            json.dumps(asdict(record), default=_json_default, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary_audit.replace(audit_path)
        temporary = self.root / "ACTIVE.tmp"
        temporary.write_text(model_id, encoding="utf-8")
        temporary.replace(active_path)
        return record

    def active_metadata(self) -> ModelArtifactMetadata:
        return self.load_active()[1]

    def load_active(self) -> tuple[TrainedReturnModel, ModelArtifactMetadata]:
        pointer = self.root / "ACTIVE"
        if not pointer.exists():
            raise FileNotFoundError("no human-approved active crypto model is registered")
        return self.load(pointer.read_text(encoding="utf-8").strip())

    def load_latest(self) -> tuple[TrainedReturnModel, ModelArtifactMetadata]:
        pointer = self.root / "LATEST"
        if not pointer.exists():
            raise FileNotFoundError("no trained crypto model is registered")
        return self.load(pointer.read_text(encoding="utf-8").strip())

    def load(self, model_id: str) -> tuple[TrainedReturnModel, ModelArtifactMetadata]:
        if re.fullmatch(r"crypto-model-[0-9a-f]{16}", model_id) is None:
            raise ValueError("invalid crypto model identifier")
        directory = self.root / model_id
        metadata_payload = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        metadata = ModelArtifactMetadata(
            model_id=str(metadata_payload["model_id"]),
            model_kind=ModelKind(metadata_payload["model_kind"]),
            feature_version=str(metadata_payload["feature_version"]),
            features=tuple(metadata_payload["features"]),
            label_horizon_days=int(metadata_payload["label_horizon_days"]),
            universe_mode=str(metadata_payload["universe_mode"]),
            dataset_hash=str(metadata_payload["dataset_hash"]),
            trained_start=datetime.fromisoformat(metadata_payload["trained_start"]),
            trained_end=datetime.fromisoformat(metadata_payload["trained_end"]),
            created_at=datetime.fromisoformat(metadata_payload["created_at"]),
            comparison=dict(metadata_payload["comparison"]),
            limitations=tuple(metadata_payload["limitations"]),
        )
        with (directory / "model.pkl").open("rb") as file:
            model = pickle.load(file)  # noqa: S301 - trusted local registry only
        if not isinstance(model, TrainedReturnModel):
            raise ValueError("registered model has an unexpected type")
        return model, metadata


def _comparison_dict(comparison: ModelComparisonResult) -> dict[str, object]:
    return {
        "selected_model": comparison.selected_model.value,
        "mean_validation_ic": comparison.mean_validation_ic,
        "mean_test_ic": comparison.mean_test_ic,
        "folds": [
            {
                "fold": fold.fold,
                "model_kind": fold.model_kind.value,
                "validation": fold.validation.to_dict(),
                "test": fold.test.to_dict(),
            }
            for fold in comparison.folds
        ],
    }


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ModelKind):
        return value.value
    raise TypeError(type(value).__name__)
