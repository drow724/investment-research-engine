from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from investment.application.services.market_data_service import MarketDataSyncResult
from investment.core.research.evaluator import EvaluationResult, QuantileMetrics
from investment.interfaces.api.fastapi.dependencies import (
    get_experiment_service,
    get_market_data_service,
    get_research_service,
)
from investment.interfaces.api.fastapi.main import create_app


class FakeMarketDataService:
    def sync(self, symbol: str, start: datetime, end: datetime) -> MarketDataSyncResult:
        return MarketDataSyncResult(symbol, 2, Path("raw.json"), Path("normalized.parquet"))


class FakeResearchService:
    def evaluate_feature(
        self, symbol: str, as_of: datetime, feature: str, label: str, quantiles: int
    ) -> EvaluationResult:
        return EvaluationResult(
            feature,
            label,
            10,
            0.2,
            0.3,
            (QuantileMetrics(1, 2, 0.1, 0.1, 0.5, -0.2),),
        )


class FakeExperimentService:
    def run(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            experiment_id="exp-fixture",
            status="COMPLETED",
            config=SimpleNamespace(
                hypothesis=kwargs["hypothesis"], features=kwargs["features"]
            ),
            results={"forward_return_30d": {"count": 20}},
            stability={"forward_return_30d": {"grouping": "YEAR"}},
            dataset_snapshot=SimpleNamespace(snapshot_id="snapshot-fixture"),
        )


def test_health_and_openapi_contract() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "service": "investment-research-engine",
        "version": "0.6.0",
    }
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/bitcoin/research/features/evaluate" in schema["paths"]
    assert "/api/v1/bitcoin/research/experiments" in schema["paths"]
    assert "/api/v1/crypto/ml/train" in schema["paths"]
    assert "/api/v1/crypto/ml/predict" in schema["paths"]
    assert "/api/v1/crypto/ml/paper/jobs/run" in schema["paths"]
    assert "/api/v1/crypto/research/experiments" in schema["paths"]
    assert "/api/v1/crypto/research/experiments/{experiment_id}/promote" in schema["paths"]


def test_bitcoin_endpoint_contracts() -> None:
    app = create_app()
    app.dependency_overrides[get_market_data_service] = lambda: FakeMarketDataService()
    app.dependency_overrides[get_research_service] = lambda: FakeResearchService()
    app.dependency_overrides[get_experiment_service] = lambda: FakeExperimentService()
    client = TestClient(app)

    sync = client.post(
        "/api/v1/bitcoin/market-data/sync",
        json={
            "symbol": "BTCUSDT",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-03T00:00:00Z",
        },
    )
    assert sync.status_code == 200
    assert sync.json()["operation"] == "market_data_sync"
    assert sync.json()["rows"] == 2

    evaluation = client.post(
        "/api/v1/bitcoin/research/features/evaluate",
        json={
            "symbol": "BTCUSDT",
            "asOf": "2024-12-31T00:00:00Z",
            "feature": "return_30d",
            "label": "forward_return_30d",
            "quantiles": 5,
        },
    )
    body = evaluation.json()
    assert evaluation.status_code == 200
    assert body["engine"] == "bitcoin"
    assert body["asOf"] == "2024-12-31T00:00:00Z"
    assert body["status"] == "COMPLETED"
    assert body["result"]["spearman_rank_ic"] == 0.3

    experiment = client.post(
        "/api/v1/bitcoin/research/experiments",
        json={
            "hypothesis": "btc_absorption_v1",
            "features": ["btc_absorption_score"],
            "labels": ["forward_return_30d"],
            "start": "2024-01-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
    )
    assert experiment.status_code == 200
    assert experiment.json()["experimentId"] == "exp-fixture"
    assert experiment.json()["datasetSnapshotId"] == "snapshot-fixture"
