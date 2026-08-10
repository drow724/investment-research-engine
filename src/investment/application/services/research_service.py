"""End-to-end point-in-time BTC feature evaluation use case."""

from datetime import datetime

from investment.bitcoin.features.defaults import default_bitcoin_features
from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.data.storage import NormalizedParquetStorage
from investment.core.feature.pipeline import FeaturePipeline
from investment.core.labels.forward import ForwardLabelGenerator
from investment.core.research.evaluator import EvaluationResult, FeatureEvaluator


class ResearchService:
    def __init__(self, storage: NormalizedParquetStorage) -> None:
        self._storage = storage

    def evaluate_feature(
        self,
        symbol: str,
        as_of: datetime,
        feature: str,
        label: str,
        quantiles: int = 5,
    ) -> EvaluationResult:
        dataset = PointInTimeDataset(self._storage.read(symbol), as_of)
        features = FeaturePipeline(default_bitcoin_features()).compute(dataset)
        labels = ForwardLabelGenerator().compute(dataset)
        research = features.join(labels, on="open_time", how="inner", validate="1:1")
        mdd_label = _matching_mdd_label(label, research.columns)
        return FeatureEvaluator().evaluate(
            research, feature=feature, label=label, quantiles=quantiles, mdd_label=mdd_label
        )


def _matching_mdd_label(return_label: str, columns: list[str]) -> str | None:
    candidate = return_label.replace("forward_return_", "forward_mdd_")
    return candidate if candidate in columns else None
