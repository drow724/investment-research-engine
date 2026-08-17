import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from investment.crypto.application.dynamic_paper_rebalance import (
    DynamicPaperRebalanceCommand,
    DynamicPaperRebalanceService,
)
from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.domain.accounting import PaperPortfolioSnapshot
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.interfaces.api.fastapi.crypto.portfolio.schemas import (
    CandidateAssessmentResponse,
    CreatePaperPortfolioRequest,
    DynamicRebalanceRequest,
    DynamicRebalanceResponse,
    PaperExecutionResponse,
    PaperPortfolioResponse,
    PaperPositionResponse,
    PaperRebalanceDecisionResponse,
    RebalanceOrderResponse,
    SelectedAssetResponse,
)
from investment.interfaces.api.fastapi.dependencies import (
    get_dynamic_paper_rebalance_service,
    get_paper_trading_service,
)

router = APIRouter(prefix="/crypto/paper/portfolios", tags=["crypto-paper-portfolio"])


@router.post("/dynamic-rebalance", response_model=DynamicRebalanceResponse)
def dynamic_rebalance(
    request: DynamicRebalanceRequest,
    service: DynamicPaperRebalanceService = Depends(get_dynamic_paper_rebalance_service),
) -> DynamicRebalanceResponse:
    try:
        result = service.run(
            DynamicPaperRebalanceCommand(request.portfolio_id, request.as_of, request.execute)
        )
    except (FileNotFoundError, KeyError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return DynamicRebalanceResponse(
        portfolio_id=result.portfolio_id,
        as_of=result.as_of,
        universe_observed_at=result.universe_observed_at,
        dry_run=result.dry_run,
        equity=str(result.equity),
        selected=tuple(
            SelectedAssetResponse(
                pair=item.pair,
                score=item.score,
                target_weight=str(item.target_weight),
                reason=item.reason,
            )
            for item in result.selected
        ),
        assessments=tuple(
            CandidateAssessmentResponse(
                pair=item.pair,
                eligible=item.eligible,
                reason=item.reason,
                score=item.score,
                average_quote_volume=(
                    str(item.average_quote_volume)
                    if item.average_quote_volume is not None
                    else None
                ),
                latest_price=(str(item.latest_price) if item.latest_price is not None else None),
            )
            for item in result.assessments
        ),
        orders=tuple(
            RebalanceOrderResponse(
                intent_id=item.intent_id,
                pair=item.pair,
                side=item.side.value,
                quantity=str(item.quantity),
                reference_price=str(item.reference_price),
                notional=str(item.notional),
                current_weight=str(item.current_weight),
                target_weight=str(item.target_weight),
                status=item.status,
            )
            for item in result.orders
        ),
        final_portfolio=_response(result.final_portfolio),
        risk_violations=result.risk_violations,
        decision_reasons=result.decision_reasons,
    )


@router.post("", response_model=PaperPortfolioResponse)
def create_portfolio(
    request: CreatePaperPortfolioRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PaperPortfolioResponse:
    try:
        snapshot = service.create_portfolio(
            request.portfolio_id,
            PortfolioPurpose(request.purpose),
            request.cash_asset,
            request.initial_cash,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _response(snapshot)


@router.get("/{portfolio_id}", response_model=PaperPortfolioResponse)
def get_portfolio(
    portfolio_id: str,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PaperPortfolioResponse:
    try:
        return _response(service.portfolio(portfolio_id))
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="portfolio not found"
        ) from error


@router.get("/{portfolio_id}/executions", response_model=tuple[PaperExecutionResponse, ...])
def get_portfolio_executions(
    portfolio_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> tuple[PaperExecutionResponse, ...]:
    try:
        records = service.executions(portfolio_id, limit)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "portfolio not found") from error
    return tuple(
        PaperExecutionResponse(
            order_id=item.order_id,
            intent_id=item.intent_id,
            pair=item.pair,
            side=item.side.value,
            quantity=str(item.quantity),
            price=str(item.price),
            notional=str(item.quantity * item.price),
            fee=str(item.fee),
            realized_pnl=str(item.realized_pnl),
            executed_at=item.executed_at,
        )
        for item in records
    )


@router.get(
    "/{portfolio_id}/rebalance-decisions",
    response_model=tuple[PaperRebalanceDecisionResponse, ...],
)
def get_rebalance_decisions(
    portfolio_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> tuple[PaperRebalanceDecisionResponse, ...]:
    try:
        records = service.rebalance_decisions(portfolio_id, limit)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "portfolio not found") from error
    return tuple(
        PaperRebalanceDecisionResponse(
            decision_id=item.decision_id,
            strategy_version=item.strategy_version,
            as_of=item.as_of,
            universe_observed_at=item.universe_observed_at,
            execute=item.execute,
            equity=str(item.equity),
            assessments=json.loads(item.assessments_json),
            selected=json.loads(item.selected_json),
            orders=json.loads(item.orders_json),
            risk_violations=item.risk_violations,
            decision_reasons=item.decision_reasons,
            status=item.status,
            created_at=item.created_at,
        )
        for item in records
    )


def _response(snapshot: PaperPortfolioSnapshot) -> PaperPortfolioResponse:
    return PaperPortfolioResponse(
        portfolio_id=snapshot.portfolio_id,
        purpose=snapshot.purpose.value,
        cash_asset=snapshot.cash_asset.symbol,
        cash_balance=snapshot.cash_balance,
        positions=tuple(
            PaperPositionResponse(
                asset=position.asset.symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
            )
            for position in snapshot.positions
        ),
        updated_at=snapshot.updated_at,
    )
