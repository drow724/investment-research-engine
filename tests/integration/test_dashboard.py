from fastapi.testclient import TestClient

from investment.interfaces.api.fastapi.dependencies import get_settings
from investment.interfaces.api.fastapi.main import create_app
from investment.interfaces.api.fastapi.settings import Settings


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        runtime_dynamic_paper_portfolio_id="paper-v2.2-analysis-main",
        runtime_dynamic_paper_execute=False,
        runtime_observation_experiment_id="paper-v2.2-decision-only-analysis-20260819",
    )
    return TestClient(app)


def test_development_dashboard_is_served() -> None:
    client = _client()
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Trading Status" in response.text
    assert "/api/v1/runtime/status" in response.text
    assert "/api/v1/crypto/market/latest" in response.text
    assert '"paperPortfolioId":"paper-v2.2-analysis-main"' in response.text
    assert "localStorage" not in response.text
    assert '"paperExecutionEnabled":false' in response.text
    assert "Paper 자동 리밸런싱" in response.text
    assert "Paper 리밸런싱·체결 이력" in response.text
    assert "전략 판단 감사 로그" in response.text
    assert "BTC 파생시장 · Short Squeeze Watch" in response.text
    assert "/api/v1/crypto/market/derivatives/squeeze" in response.text
    assert "crypto_derivatives_snapshot" in response.text
    assert "거래 체결/미체결 이유" in response.text
    assert "NEW_BUYS_BLOCKED_BY_DAILY_RISK_BUDGET" in response.text
    assert "crypto_dynamic_paper_shadow_rebalance" in response.text
    assert "실거래 주문은 전송되지 않습니다" in response.text


def test_observation_diagnostics_dashboard_is_served() -> None:
    client = _client()
    response = client.get("/diagnostics")

    assert response.status_code == 200
    assert "v2 Signal Diagnostics" in response.text
    assert "후보별 판단 상세" in response.text
    assert "V2.4 BTC 파생·수급 컨텍스트" in response.text
    assert "수수료 후" in response.text
    assert "관찰 실험 선택" in response.text
    assert "/api/v1/experiments" in response.text
    assert '"experimentId":"paper-v2.2-decision-only-analysis-20260819"' in response.text
