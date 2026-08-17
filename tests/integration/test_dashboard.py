from fastapi.testclient import TestClient

from investment.interfaces.api.fastapi.main import create_app


def test_development_dashboard_is_served() -> None:
    client = TestClient(create_app())
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Trading Status" in response.text
    assert "/api/v1/runtime/status" in response.text
    assert "/api/v1/crypto/market/latest" in response.text
    assert '"paperPortfolioId":"paper-analysis-main"' in response.text
    assert '"paperExecutionEnabled":false' in response.text
    assert "Paper 자동 리밸런싱" in response.text
    assert "Paper 리밸런싱·체결 이력" in response.text
    assert "전략 판단 감사 로그" in response.text
    assert "거래 체결/미체결 이유" in response.text
    assert "NEW_BUYS_BLOCKED_BY_DAILY_RISK_BUDGET" in response.text
    assert "실거래 주문은 전송되지 않습니다" in response.text


def test_observation_diagnostics_dashboard_is_served() -> None:
    client = TestClient(create_app())
    response = client.get("/diagnostics")

    assert response.status_code == 200
    assert "v2 Signal Diagnostics" in response.text
    assert "후보별 판단 상세" in response.text
    assert "관찰 실험 선택" in response.text
    assert "/api/v1/experiments" in response.text
    assert '"experimentId":"paper-v2.1-decision-only-analysis-20260816"' in response.text
