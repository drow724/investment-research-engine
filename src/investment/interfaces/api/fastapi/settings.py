"""Runtime settings for adapter wiring."""

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVESTMENT_", extra="ignore")

    raw_data_root: Path = Path("data/raw")
    normalized_data_root: Path = Path("data/normalized")
    experiment_output_root: Path = Path("experiments/output")
    crypto_price_root: Path = Path("data/normalized/crypto/price")
    crypto_raw_price_root: Path = Path("data/raw/crypto/price")
    crypto_universe_root: Path = Path("data/normalized/crypto/universe")
    crypto_paper_database: Path = Path("data/paper/crypto-trading.sqlite3")
    crypto_model_root: Path = Path("models/crypto")
    crypto_research_lifecycle_root: Path = Path("experiments/crypto-lifecycle")
    binance_base_url: str = "https://api.binance.com"
    upbit_base_url: str = "https://api.upbit.com"
    crypto_universe_lookback_days: int = 30
    crypto_minimum_average_quote_volume: Decimal = Decimal("1000000000")
    crypto_maximum_universe_assets: int = 30
