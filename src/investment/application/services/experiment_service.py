"""Synchronous Phase 2 experiment use case behind a job-ready boundary."""

from datetime import datetime

import polars as pl

from investment.bitcoin.research.hypotheses import HYPOTHESES
from investment.bitcoin.research.intelligence import BitcoinIntelligenceBuilder
from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.data.storage import MetricParquetStorage, NormalizedParquetStorage
from investment.core.domain.experiment import DatasetSnapshot, ExperimentConfig
from investment.core.labels.forward import ForwardLabelGenerator
from investment.core.research.experiment import ExperimentRegistry, ExperimentRun, ExperimentRunner


class ExperimentService:
    def __init__(
        self,
        price_storage: NormalizedParquetStorage,
        metric_storage: MetricParquetStorage,
        registry: ExperimentRegistry,
    ) -> None:
        self._price_storage = price_storage
        self._metric_storage = metric_storage
        self._registry = registry

    def run(
        self,
        *,
        hypothesis: str,
        features: tuple[str, ...],
        labels: tuple[str, ...],
        start: datetime,
        end: datetime,
        symbol: str = "BTCUSDT",
        quantiles: int = 5,
    ) -> ExperimentRun:
        if hypothesis not in HYPOTHESES:
            raise ValueError(f"unknown hypothesis: {hypothesis}")
        price = self._price_storage.read(symbol)
        metric_frames = {
            name: self._read_optional_metric(name)
            for name in ("etf", "onchain", "derivatives")
        }
        intelligence = BitcoinIntelligenceBuilder().build(
            price,
            as_of=end,
            etf=metric_frames["etf"],
            onchain=metric_frames["onchain"],
            derivatives=metric_frames["derivatives"],
        )
        price_dataset = PointInTimeDataset(price, end)
        labels_frame = ForwardLabelGenerator().compute(price_dataset)
        research = intelligence.join(labels_frame, on="open_time", how="left", validate="1:1")
        selected = research.filter(
            (pl.col("open_time") >= start) & (pl.col("open_time") < end)
        )
        sources = tuple(
            sorted(
                {
                    *price.get_column("source").unique().to_list(),
                    *self._metric_sources(metric_frames),
                }
            )
        )
        snapshot = DatasetSnapshot.from_frame(
            selected,
            sources=sources,
            schema_version="bitcoin-intelligence-v1",
        )
        config = ExperimentConfig(
            hypothesis=hypothesis,
            features=features,
            labels=labels,
            start_time=start,
            end_time=end,
            parameters={"quantiles": quantiles, "point_in_time_cutoff": end.isoformat()},
        )
        run = ExperimentRunner().run(
            research,
            config,
            snapshot,
            feature_versions=dict.fromkeys(features, "v1"),
        )
        self._registry.save(run)
        return run

    def _read_optional_metric(self, dataset: str) -> pl.DataFrame | None:
        try:
            return self._metric_storage.read(dataset)
        except FileNotFoundError:
            return None

    @staticmethod
    def _metric_sources(frames: dict[str, pl.DataFrame | None]) -> set[str]:
        sources: set[str] = set()
        for frame in frames.values():
            if frame is not None and "source" in frame.columns:
                sources.update(frame.get_column("source").unique().to_list())
        return sources
