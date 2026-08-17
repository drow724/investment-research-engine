from fastapi.testclient import TestClient

from investment.interfaces.api.fastapi.main import create_app


def test_frozen_observation_read_only_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "paper-observation")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE", "false")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "frozen-test")

    with TestClient(create_app()) as client:
        current = client.get("/api/v1/experiments/current")
        experiments = client.get("/api/v1/experiments")
        health = client.get("/api/v1/experiments/frozen-test/health")
        metrics = client.get("/api/v1/experiments/frozen-test/metrics")
        decisions = client.get("/api/v1/experiments/frozen-test/decisions")
        report = client.get("/api/v1/experiments/frozen-test/report")
        diagnostics = client.get("/api/v1/experiments/frozen-test/diagnostics")

    assert current.status_code == 200
    assert experiments.status_code == 200
    assert experiments.json()[0]["experiment_id"] == "frozen-test"
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
    assert diagnostics.json()["horizonSummary"] == []
