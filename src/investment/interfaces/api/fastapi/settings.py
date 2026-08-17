"""Runtime settings for adapter wiring."""

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVESTMENT_", env_file=".env", extra="ignore")

    raw_data_root: Path = Path("data/raw")
    normalized_data_root: Path = Path("data/normalized")
    experiment_output_root: Path = Path("experiments/output")
    crypto_price_root: Path = Path("data/normalized/crypto/price")
    crypto_raw_price_root: Path = Path("data/raw/crypto/price")
    crypto_universe_root: Path = Path("data/normalized/crypto/universe")
    crypto_paper_database: Path = Path("data/paper/crypto-trading.sqlite3")
    crypto_observation_database: Path = Path("data/observations/crypto-forward.sqlite3")
    crypto_model_root: Path = Path("models/crypto")
    crypto_research_lifecycle_root: Path = Path("experiments/crypto-lifecycle")
    runtime_state_root: Path = Path("runtime/state")
    runtime_instance_id: str = "investment-engine-01"
    runtime_event_endpoint: str | None = None
    runtime_event_retry_delays_json: str = "[1, 5, 30]"
    runtime_event_timeout_seconds: float = 5.0
    runtime_heartbeat_cron: str = "* * * * *"
    runtime_universe_snapshot_cron: str = "5 0 * * *"
    runtime_market_sync_cron: str = "15 0 * * *"
    runtime_market_sync_pairs_json: str = '["BTC/KRW", "ETH/KRW", "SOL/KRW"]'
    runtime_market_sync_lookback_days: int = 7
    runtime_intraday_sync_cron: str = "1,16,31,46 * * * *"
    runtime_intraday_sync_lookback_hours: int = 6
    runtime_intraday_maximum_assets: int = 50
    runtime_dynamic_rebalance_cron: str = "2,17,32,47 * * * *"
    runtime_dynamic_paper_portfolio_id: str | None = None
    runtime_dynamic_paper_execute: bool = False
    runtime_dynamic_paper_initial_cash: Decimal = Decimal("1000000")
    runtime_observation_experiment_id: str | None = None
    runtime_observation_outcome_cron: str = "7,22,37,52 * * * *"
    binance_base_url: str = "https://api.binance.com"
    upbit_base_url: str = "https://api.upbit.com"
    crypto_universe_lookback_days: int = 30
    crypto_minimum_average_quote_volume: Decimal = Decimal("1000000000")
    crypto_maximum_universe_assets: int = 30
