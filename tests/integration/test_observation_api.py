from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from investment.crypto.observation.domain import ObservationExperiment, ObservationStatus
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.interfaces.api.fastapi.main import create_app


def test_frozen_observation_read_only_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "paper-observation")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE", "false")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_STRATEGY_VERSION", "dynamic-intraday-v2.1")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "frozen-test")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_DRAIN_EXPERIMENT_IDS_JSON", "[]")

    with TestClient(create_app()) as client:
        started = client.post(
            "/api/v1/experiments",
            json={"experimentId": "frozen-test", "portfolioId": "paper-observation"},
        )
        restarted = client.post(
            "/api/v1/experiments",
            json={"experimentId": "frozen-test", "portfolioId": "paper-observation"},
        )
        other_portfolio = client.post(
            "/api/v1/crypto/paper/portfolios",
            json={
                "portfolioId": "paper-other",
                "purpose": "PAPER_TRADING",
                "cashAsset": "KRW",
                "initialCash": "1000000",
            },
        )
        conflicting_restart = client.post(
            "/api/v1/experiments",
            json={"experimentId": "frozen-test", "portfolioId": "paper-other"},
        )
        current = client.get("/api/v1/experiments/current")
        experiments = client.get("/api/v1/experiments")
        experiment_status = client.get("/api/v1/experiments/frozen-test")
        health = client.get("/api/v1/experiments/frozen-test/health")
        metrics = client.get("/api/v1/experiments/frozen-test/metrics")
        decisions = client.get("/api/v1/experiments/frozen-test/decisions")
        report = client.get("/api/v1/experiments/frozen-test/report")
        diagnostics = client.get("/api/v1/experiments/frozen-test/diagnostics")
        missing_contexts = client.get("/api/v1/experiments/missing/market-contexts")

    assert started.status_code == 200
    assert started.json()["experimentId"] == "frozen-test"
    assert started.json()["portfolioId"] == "paper-observation"
    assert started.json()["strategyVersion"] == "dynamic-intraday-v2.1"
    assert started.json()["configHash"]
    assert started.json()["status"] == "RUNNING"
    assert restarted.status_code == 200
    assert restarted.json() == started.json()
    assert other_portfolio.status_code == 200
    assert conflicting_restart.status_code == 409
    assert "identity does not match" in conflicting_restart.json()["detail"]
    assert current.status_code == 200
    assert experiments.status_code == 200
    assert experiments.json()[0]["experiment_id"] == "frozen-test"
    assert experiment_status.status_code == 200
    assert experiment_status.json()["experimentId"] == "frozen-test"
    assert experiment_status.json()["status"] == "RUNNING"
    assert current.json()["strategy_version"] == "dynamic-intraday-v2.1"
    assert health.status_code == 200
    assert health.json()["actualDecisionCycles"] == 0
    assert metrics.status_code == 200
    assert metrics.json()["trades"]["completedTrades"] == 0
    assert decisions.json() == []
    assert report.status_code == 200
    assert "signalQuality" in report.json()
    assert diagnostics.status_code == 200
    assert diagnostics.json()["recentCandidates"] == []
    assert diagnostics.json()["recentMarketContexts"] == []
    assert diagnostics.json()["horizonSummary"] == []
    assert missing_contexts.status_code == 404


def test_observation_start_requires_existing_portfolio(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", raising=False)
    monkeypatch.delenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", raising=False)

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/experiments",
            json={"experimentId": "new-observation", "portfolioId": "missing"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "paper portfolio not found"


def test_runtime_rolls_v21_into_isolated_v22_and_keeps_history_readable(
    tmp_path, monkeypatch
) -> None:
    observation_path = tmp_path / "observation.sqlite3"
    origin = datetime(2026, 8, 19, tzinfo=UTC)
    SqliteObservationRepository(observation_path).save_experiment(
        ObservationExperiment(
            "old-v21",
            "old-paper",
            "dynamic-intraday-v2.1",
            "legacy-hash",
            origin,
            origin + timedelta(days=7),
            ObservationStatus.RUNNING,
            1_000_000,
        )
    )
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(observation_path))
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "new-v22-paper")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_STRATEGY_VERSION", "dynamic-intraday-v2.2")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "new-v22")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_DRAIN_EXPERIMENT_IDS_JSON", '["old-v21"]')

    with TestClient(create_app()) as client:
        current = client.get("/api/v1/experiments/current")
        historical = client.get("/api/v1/experiments/old-v21")

    assert current.json()["experiment_id"] == "new-v22"
    assert current.json()["strategy_version"] == "dynamic-intraday-v2.2"
    assert historical.json()["strategyVersion"] == "dynamic-intraday-v2.1"
    assert historical.json()["status"] == "INTERRUPTED"
    assert historical.json()["interruptionReason"] == "superseded by new-v22"
