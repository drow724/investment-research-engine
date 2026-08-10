"""Reproducible experiment and dataset snapshot models."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import polars as pl

from investment.core.domain.observation import require_utc


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    snapshot_id: str
    sources: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    generated_at: datetime
    schema_version: str
    content_hash: str
    row_count: int

    @classmethod
    def from_frame(
        cls,
        frame: pl.DataFrame,
        *,
        sources: tuple[str, ...],
        schema_version: str,
        generated_at: datetime | None = None,
        time_column: str = "open_time",
    ) -> "DatasetSnapshot":
        if frame.is_empty():
            raise ValueError("cannot snapshot an empty dataset")
        ordered = frame.sort(time_column)
        content = ordered.hash_rows(seed=0).to_numpy().tobytes()
        schema_bytes = repr(ordered.schema).encode()
        content_hash = hashlib.sha256(schema_bytes + content).hexdigest()
        start = ordered.get_column(time_column).min()
        end = ordered.get_column(time_column).max()
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise ValueError("snapshot time column must contain datetimes")
        identity = json.dumps(
            {
                "sources": sorted(sources),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "schema_version": schema_version,
                "content_hash": content_hash,
                "rows": ordered.height,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_id = f"snapshot-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        return cls(
            snapshot_id=snapshot_id,
            sources=tuple(sorted(sources)),
            start_time=start,
            end_time=end,
            generated_at=generated_at or datetime.now(UTC),
            schema_version=schema_version,
            content_hash=content_hash,
            row_count=ordered.height,
        )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    name: str
    statement: str
    feature: str
    version: str = "v1"


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    hypothesis: str
    features: tuple[str, ...]
    labels: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    parameters: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time", require_utc(self.start_time, "start_time"))
        object.__setattr__(self, "end_time", require_utc(self.end_time, "end_time"))
        if self.start_time >= self.end_time:
            raise ValueError("experiment start must precede end")
        if not self.features or not self.labels:
            raise ValueError("experiment requires features and labels")

    def canonical_json(self) -> str:
        payload = asdict(self)
        payload["start_time"] = self.start_time.isoformat()
        payload["end_time"] = self.end_time.isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
