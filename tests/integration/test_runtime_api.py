from fastapi.testclient import TestClient

from investment.interfaces.api.fastapi.main import create_app


def test_runtime_status_and_job_history_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    with TestClient(create_app()) as client:
        status = client.get("/api/v1/runtime/status")
        jobs = client.get("/api/v1/jobs")

    assert status.status_code == 200
    assert status.json()["status"] == "IDLE"
    assert status.json()["instanceId"] == "investment-engine-01"
    assert jobs.status_code == 200
    assert jobs.json() == []
