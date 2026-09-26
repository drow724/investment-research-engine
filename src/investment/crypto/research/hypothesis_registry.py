"""Append-only hypothesis records with one-way OOS consumption."""

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    REJECTED = "REJECTED"
    VALIDATED = "VALIDATED"
    OOS_PENDING = "OOS_PENDING"
    OOS_FAILED = "OOS_FAILED"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


@dataclass(frozen=True, slots=True)
class ResearchHypothesis:
    hypothesis_id: str
    description: str
    signal_ids: tuple[str, ...]
    prediction_horizon_minutes: int
    universe: str
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    train_period: str
    validation_period: str
    oos_period: str
    status: HypothesisStatus
    created_at: datetime
    parent_hypothesis_id: str | None = None
    result_summary: str | None = None
    rejection_reason: str | None = None
    oos_exposed_at: datetime | None = None


class HypothesisRegistry:
    def __init__(self, root: str | Path = "experiments/hypotheses") -> None:
        self.root = Path(root)

    def create(self, hypothesis: ResearchHypothesis) -> Path:
        if hypothesis.prediction_horizon_minutes <= 0:
            raise ValueError("prediction horizon must be positive")
        path = self._path(hypothesis.hypothesis_id)
        if path.exists():
            raise FileExistsError(hypothesis.hypothesis_id)
        self._write(path, hypothesis)
        return path

    def load(self, hypothesis_id: str) -> ResearchHypothesis:
        value = json.loads(self._path(hypothesis_id).read_text(encoding="utf-8"))
        return ResearchHypothesis(
            hypothesis_id=str(value["hypothesis_id"]),
            description=str(value["description"]),
            signal_ids=tuple(value["signal_ids"]),
            prediction_horizon_minutes=int(value["prediction_horizon_minutes"]),
            universe=str(value["universe"]),
            primary_metric=str(value["primary_metric"]),
            secondary_metrics=tuple(value["secondary_metrics"]),
            train_period=str(value["train_period"]),
            validation_period=str(value["validation_period"]),
            oos_period=str(value["oos_period"]),
            status=HypothesisStatus(value["status"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            parent_hypothesis_id=value.get("parent_hypothesis_id"),
            result_summary=value.get("result_summary"),
            rejection_reason=value.get("rejection_reason"),
            oos_exposed_at=(
                datetime.fromisoformat(value["oos_exposed_at"])
                if value.get("oos_exposed_at")
                else None
            ),
        )

    def expose_oos(self, hypothesis_id: str) -> ResearchHypothesis:
        current = self.load(hypothesis_id)
        if current.oos_exposed_at is not None:
            raise ValueError("OOS period has already been consumed")
        updated = replace(current, oos_exposed_at=datetime.now(UTC))
        self._write(self._path(hypothesis_id), updated)
        return updated

    def _path(self, identity: str) -> Path:
        if not identity.strip() or "/" in identity or ".." in identity:
            raise ValueError("invalid hypothesis id")
        return self.root / f"{identity}.json"

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, StrEnum):
            return value.value
        raise TypeError(type(value).__name__)

    def _write(self, path: Path, hypothesis: ResearchHypothesis) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                asdict(hypothesis),
                default=self._json_default,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
