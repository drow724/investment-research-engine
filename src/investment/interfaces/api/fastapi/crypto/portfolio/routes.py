from fastapi import APIRouter, Depends, HTTPException, status

from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.domain.accounting import PaperPortfolioSnapshot
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.interfaces.api.fastapi.crypto.portfolio.schemas import (
    CreatePaperPortfolioRequest,
    PaperPortfolioResponse,
    PaperPositionResponse,
)
from investment.interfaces.api.fastapi.dependencies import get_paper_trading_service

router = APIRouter(prefix="/crypto/paper/portfolios", tags=["crypto-paper-portfolio"])


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
