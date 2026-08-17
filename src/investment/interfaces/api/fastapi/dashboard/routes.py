import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from investment.interfaces.api.fastapi.dependencies import get_settings
from investment.interfaces.api.fastapi.settings import Settings

router = APIRouter(tags=["development-dashboard"])
_DASHBOARD = Path(__file__).with_name("trading-dashboard.html")
_DIAGNOSTICS = Path(__file__).with_name("observation-diagnostics.html")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    config = json.dumps(
        {
            "paperPortfolioId": settings.runtime_dynamic_paper_portfolio_id or "paper-main",
            "paperExecutionEnabled": settings.runtime_dynamic_paper_execute,
            "paperRebalanceCron": settings.runtime_dynamic_rebalance_cron,
        },
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    content = _DASHBOARD.read_text(encoding="utf-8").replace("__DASHBOARD_CONFIG__", config)
    return HTMLResponse(content)


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics_dashboard(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    config = json.dumps(
        {"experimentId": settings.runtime_observation_experiment_id}, separators=(",", ":")
    ).replace("<", "\\u003c")
    content = _DIAGNOSTICS.read_text(encoding="utf-8").replace(
        "__DIAGNOSTICS_CONFIG__", config
    )
    return HTMLResponse(content)
