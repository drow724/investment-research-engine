from fastapi import APIRouter, Depends, HTTPException, status

from investment.crypto.application.backtest_service import (
    CryptoBacktestService,
    MomentumBacktestCommand,
)
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.interfaces.api.fastapi.crypto.backtest.schemas import (
    EquityPointResponse,
    MomentumBacktestRequest,
    MomentumBacktestResponse,
    PerformanceMetricsResponse,
)
from investment.interfaces.api.fastapi.dependencies import get_crypto_backtest_service

router = APIRouter(prefix="/crypto/backtests", tags=["crypto-backtests"])


@router.post("", response_model=MomentumBacktestResponse)
def run_momentum_backtest(
    request: MomentumBacktestRequest,
    service: CryptoBacktestService = Depends(get_crypto_backtest_service),
) -> MomentumBacktestResponse:
    try:
        result = service.run_momentum(
            MomentumBacktestCommand(
                pair_symbols=request.pairs,
                start=request.start,
                end=request.end,
                initial_capital=request.initial_capital,
                purpose=PortfolioPurpose(request.portfolio_purpose),
                lookback_days=request.lookback_days,
                maximum_positions=request.maximum_positions,
                rebalance_days=request.rebalance_days,
                minimum_average_quote_volume=request.minimum_average_quote_volume,
                fee_rate=request.fee_rate,
                slippage_rate=request.slippage_rate,
            )
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="normalized crypto market data is unavailable",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return MomentumBacktestResponse(
        strategy=result.strategy_name,
        strategy_version=result.strategy_version,
        portfolio_id=result.portfolio_id,
        portfolio_purpose=result.purpose.value,
        start=result.start,
        end=result.end,
        metrics=PerformanceMetricsResponse.model_validate(
            result.metrics, from_attributes=True
        ),
        equity_curve=tuple(
            EquityPointResponse.model_validate(point, from_attributes=True)
            for point in result.equity_curve
        ),
        rebalance_count=result.rebalance_count,
        rejected_rebalances=result.rejected_rebalances,
    )
