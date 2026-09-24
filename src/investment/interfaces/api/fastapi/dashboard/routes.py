import json
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from investment.interfaces.api.fastapi.dependencies import get_settings
from investment.interfaces.api.fastapi.settings import Settings

router = APIRouter(tags=["development-dashboard"])
_DASHBOARD = Path(__file__).with_name("trading-dashboard.html")
_DIAGNOSTICS = Path(__file__).with_name("observation-diagnostics.html")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    active_shadow_experiment_id = getattr(
        request.app.state,
        "shadow_observation_experiment_id",
        None,
    )
    shadow_startup_error = getattr(request.app.state, "shadow_startup_error", None)
    derivatives_startup_error = getattr(
        request.app.state,
        "derivatives_startup_error",
        None,
    )
    config = json.dumps(
        {
            "paperPortfolioId": settings.runtime_dynamic_paper_portfolio_id or "paper-main",
            "paperExecutionEnabled": settings.runtime_dynamic_paper_execute,
            "paperRebalanceCron": settings.runtime_dynamic_rebalance_cron,
            "primaryStrategyVersion": settings.runtime_dynamic_strategy_version,
            "shadowStrategyVersion": (
                settings.runtime_shadow_dynamic_strategy_version
                if active_shadow_experiment_id is not None
                else None
            ),
            "shadowPortfolioId": (
                settings.runtime_shadow_dynamic_paper_portfolio_id
                if active_shadow_experiment_id is not None
                else None
            ),
            "shadowExperimentId": active_shadow_experiment_id,
            "shadowStartupError": shadow_startup_error,
            "derivativesStartupError": derivatives_startup_error,
            "shadowRebalanceCron": settings.runtime_shadow_dynamic_rebalance_cron,
        },
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    content = _DASHBOARD.read_text(encoding="utf-8").replace("__DASHBOARD_CONFIG__", config)
    v28_suite = getattr(request.app.state, "v28_paper_experiments", [])
    v29_suite = getattr(request.app.state, "v29_paper_experiments", [])
    suite = (*v28_suite, *v29_suite)
    cards = "".join(
        f'<div class="item"><a href="/diagnostics?experimentId={escape(item, quote=True)}">'
        f'{escape(item)}</a><p class="muted tiny">Portfolio: {escape(item)}-paper</p></div>'
        for item in suite
    )
    content = content.replace(
        "__V28_EXPERIMENTS__",
        '<section class="card"><h2>독립 Paper 비교 실험</h2>'
        '<p class="muted">각 100만원 · 15분 판단 · 7일 후 자동 거래 중단 · '
        '링크를 눌러 각 실험 판단과 성과를 확인하세요.</p>'
        f'<div class="stack">{cards}</div></section>' if suite else "",
    )
    return HTMLResponse(content)


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics_dashboard(
    experiment_id: str | None = Query(default=None, alias="experimentId"),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    selected_experiment = experiment_id or settings.runtime_observation_experiment_id
    config = json.dumps({"experimentId": selected_experiment}, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    content = _DIAGNOSTICS.read_text(encoding="utf-8").replace("__DIAGNOSTICS_CONFIG__", config)
    return HTMLResponse(content)
