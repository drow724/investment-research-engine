"""Runtime settings for adapter wiring."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
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
    crypto_derivatives_database: Path = Path("data/observations/crypto-derivatives.sqlite3")
    crypto_model_root: Path = Path("models/crypto")
    crypto_research_lifecycle_root: Path = Path("experiments/crypto-lifecycle")
    crypto_strategy_config_root: Path = Path("config/strategies")
    crypto_strategy_review_root: Path = Path("experiments/strategy-review")
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
    runtime_intraday_maximum_assets: int = 300
    runtime_intraday_sync_budget_seconds: int = Field(default=180, gt=0, lt=900)
    runtime_derivatives_snapshot_cron: str = "3,18,33,48 * * * *"
    runtime_derivatives_websocket_enabled: bool = False
    runtime_dynamic_rebalance_cron: str = "5,20,35,50 * * * *"
    runtime_dynamic_strategy_version: str = "dynamic-intraday-v2.1"
    runtime_dynamic_paper_portfolio_id: str | None = None
    runtime_dynamic_paper_execute: bool = False
    runtime_paper_execution_model_version: str = "paper-fill-v1"
    runtime_dynamic_paper_initial_cash: Decimal = Decimal("1000000")
    runtime_shadow_dynamic_rebalance_cron: str = "6,21,36,51 * * * *"
    runtime_shadow_dynamic_strategy_version: str | None = None
    runtime_shadow_dynamic_paper_portfolio_id: str | None = None
    runtime_shadow_dynamic_paper_execute: bool = False
    runtime_shadow_observation_experiment_id: str | None = None
    runtime_observation_experiment_id: str | None = None
    runtime_v28_paper_experiment_prefix: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$"
    )
    runtime_v29_paper_experiment_prefix: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$"
    )
    runtime_observation_drain_experiment_ids_json: str = "[]"
    runtime_observation_outcome_cron: str = "10,25,40,55 * * * *"
    runtime_strategy_review_cron: str = "12 1 * * *"
    binance_base_url: str = "https://api.binance.com"
    binance_futures_base_url: str = "https://fapi.binance.com"
    binance_liquidation_websocket_url: str = (
        "wss://fstream.binance.com/market/ws/btcusdt@forceOrder"
    )
    binance_mark_price_websocket_url: str = (
        "wss://fstream.binance.com/market/ws/btcusdt@markPrice@1s"
    )
    coinbase_market_websocket_url: str = "wss://advanced-trade-ws.coinbase.com"
    upbit_base_url: str = "https://api.upbit.com"
    crypto_universe_lookback_days: int = 30
    crypto_minimum_average_quote_volume: Decimal = Decimal("1000000000")
    crypto_maximum_universe_assets: int = 30

    @property
    def runtime_shadow_dynamic_enabled(self) -> bool:
        return self.runtime_shadow_dynamic_strategy_version is not None

    @field_validator(
        "runtime_event_endpoint",
        "runtime_dynamic_paper_portfolio_id",
        "runtime_observation_experiment_id",
        "runtime_v28_paper_experiment_prefix",
        "runtime_v29_paper_experiment_prefix",
        "runtime_shadow_dynamic_strategy_version",
        "runtime_shadow_dynamic_paper_portfolio_id",
        "runtime_shadow_observation_experiment_id",
        mode="before",
    )
    @classmethod
    def normalize_optional_runtime_value(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_shadow_dynamic_lane(self) -> Self:
        """Keep the optional challenger complete, isolated, and decision-only."""

        if self.runtime_paper_execution_model_version not in {"paper-fill-v1", "paper-fill-v2"}:
            raise ValueError("runtime paper execution model must be paper-fill-v1 or paper-fill-v2")
        if (
            self.runtime_v28_paper_experiment_prefix is not None
            and self.runtime_v28_paper_experiment_prefix
            == self.runtime_v29_paper_experiment_prefix
        ):
            raise ValueError("V2.8 and V2.9 experiment prefixes must differ")
        identities = {
            "strategy version": self.runtime_shadow_dynamic_strategy_version,
            "Paper portfolio ID": self.runtime_shadow_dynamic_paper_portfolio_id,
            "observation experiment ID": self.runtime_shadow_observation_experiment_id,
        }
        configured = {name for name, value in identities.items() if value is not None}
        if configured and len(configured) != len(identities):
            missing = ", ".join(sorted(set(identities) - configured))
            raise ValueError(
                f"shadow dynamic lane requires all identity settings; missing: {missing}"
            )
        if self.runtime_shadow_dynamic_paper_execute:
            raise ValueError("shadow dynamic lane is decision-only; Paper execution must be false")
        if not configured:
            return self

        for name, value in identities.items():
            if value is None or not value.strip():
                raise ValueError(f"shadow dynamic {name} must not be blank")
        if len(self.runtime_shadow_dynamic_rebalance_cron.split()) != 5:
            raise ValueError("shadow dynamic rebalance cron must contain five fields")
        if self.runtime_shadow_dynamic_strategy_version == self.runtime_dynamic_strategy_version:
            raise ValueError("shadow and primary strategy versions must differ")
        if (
            self.runtime_shadow_dynamic_paper_portfolio_id
            == self.runtime_dynamic_paper_portfolio_id
        ):
            raise ValueError("shadow and primary Paper portfolio IDs must differ")
        if self.runtime_shadow_observation_experiment_id == self.runtime_observation_experiment_id:
            raise ValueError("shadow and primary observation experiment IDs must differ")
        try:
            drain_ids = json.loads(self.runtime_observation_drain_experiment_ids_json)
        except json.JSONDecodeError as error:
            raise ValueError("observation drain experiment IDs must be valid JSON") from error
        if not isinstance(drain_ids, list) or not all(isinstance(item, str) for item in drain_ids):
            raise ValueError("observation drain experiment IDs must be a JSON string list")
        if self.runtime_shadow_observation_experiment_id in drain_ids:
            raise ValueError("active shadow observation cannot also be a drain experiment")
        return self
