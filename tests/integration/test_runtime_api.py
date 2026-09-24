from fastapi.testclient import TestClient

from investment.interfaces.api.fastapi.main import create_app


def test_runtime_status_and_job_history_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_CRYPTO_STRATEGY_REVIEW_ROOT", str(tmp_path / "strategy-review"))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "runtime-paper")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "runtime-exp")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_DRAIN_EXPERIMENT_IDS_JSON", "[]")
    with TestClient(create_app()) as client:
        status = client.get("/api/v1/runtime/status")
        jobs = client.get("/api/v1/jobs")
        configured = {item.name for item in client.app.state.autonomous_runtime.scheduler.configs}
        review = client.post("/api/v1/jobs/crypto_strategy_review/run")

    assert status.status_code == 200
    assert status.json()["status"] == "IDLE"
    assert status.json()["instanceId"] == "investment-engine-01"
    assert jobs.status_code == 200
    assert jobs.json() == []
    assert "crypto_strategy_review" in configured
    assert review.status_code == 200
    assert review.json()["execution"]["status"] == "COMPLETED"
    assert len(tuple((tmp_path / "strategy-review" / "analyses").glob("*.json"))) == 1


def test_drain_only_observation_evaluator_remains_scheduled(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.delenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", raising=False)
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_DRAIN_EXPERIMENT_IDS_JSON", '["drain-only"]')

    with TestClient(create_app()) as client:
        configured = {item.name for item in client.app.state.autonomous_runtime.scheduler.configs}

    assert "crypto_observation_outcome_evaluation" in configured
