from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from investment.crypto.observation.domain import (
    ObservationExperiment,
    ObservationStatus,
)
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.interfaces.api.fastapi.main import create_app


def _configure_lanes(monkeypatch, tmp_path) -> tuple[str, str]:
    observation_path = tmp_path / "observation.sqlite3"
    paper_path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(observation_path))
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(paper_path))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_STRATEGY_VERSION", "dynamic-intraday-v2.3")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "primary-paper")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE", "false")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "primary-experiment")
    monkeypatch.setenv(
        "INVESTMENT_RUNTIME_SHADOW_DYNAMIC_STRATEGY_VERSION",
        "dynamic-intraday-v2.4",
    )
    monkeypatch.setenv(
        "INVESTMENT_RUNTIME_SHADOW_DYNAMIC_PAPER_PORTFOLIO_ID",
        "shadow-paper",
    )
    monkeypatch.setenv(
        "INVESTMENT_RUNTIME_SHADOW_OBSERVATION_EXPERIMENT_ID",
        "shadow-experiment",
    )
    return str(observation_path), str(paper_path)


def test_shadow_identity_conflict_degrades_only_shadow_lane(monkeypatch, tmp_path) -> None:
    observation_path, _ = _configure_lanes(monkeypatch, tmp_path)
    started = datetime.now(UTC) - timedelta(hours=1)
    SqliteObservationRepository(observation_path).save_experiment(
        ObservationExperiment(
            "shadow-experiment",
            "shadow-paper",
            "dynamic-intraday-v2.4",
            "incompatible-old-hash",
            started,
            started + timedelta(hours=168),
            ObservationStatus.RUNNING,
            1_000_000,
        )
    )

    with TestClient(create_app()) as client:
        health = client.get("/api/v1/health")
        shadow_job = client.post("/api/v1/jobs/crypto_dynamic_paper_shadow_rebalance/run")
        dashboard = client.get("/dashboard")

    assert health.status_code == 200
    assert shadow_job.status_code == 404
    assert "shadow lane initialization failed" in dashboard.text
    assert '"shadowExperimentId":null' in dashboard.text


def test_generic_observation_start_cannot_claim_configured_shadow_portfolio(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_lanes(monkeypatch, tmp_path)

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/experiments",
            json={"experimentId": "wrong-shadow-experiment", "portfolioId": "shadow-paper"},
        )

    assert response.status_code == 409
    assert "owned by its frozen shadow experiment" in response.json()["detail"]
