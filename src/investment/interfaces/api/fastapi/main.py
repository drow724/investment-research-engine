"""FastAPI application factory and default ASGI application."""

from fastapi import FastAPI

from investment import __version__
from investment.interfaces.api.fastapi.crypto.backtest import routes as crypto_backtests
from investment.interfaces.api.fastapi.crypto.market import routes as crypto_market
from investment.interfaces.api.fastapi.crypto.ml import routes as crypto_ml
from investment.interfaces.api.fastapi.crypto.portfolio import routes as crypto_portfolios
from investment.interfaces.api.fastapi.crypto.research import routes as crypto_research
from investment.interfaces.api.fastapi.routers import bitcoin, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="Investment Research Engine",
        description="Point-in-time-safe investment research and crypto backtest operations.",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(bitcoin.router, prefix="/api/v1")
    app.include_router(crypto_backtests.router, prefix="/api/v1")
    app.include_router(crypto_market.router, prefix="/api/v1")
    app.include_router(crypto_ml.router, prefix="/api/v1")
    app.include_router(crypto_portfolios.router, prefix="/api/v1")
    app.include_router(crypto_research.router, prefix="/api/v1")
    return app


app = create_app()
