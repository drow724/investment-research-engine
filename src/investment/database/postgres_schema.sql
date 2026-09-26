CREATE SCHEMA IF NOT EXISTS {schema};
SET search_path TO {schema};

CREATE TABLE IF NOT EXISTS paper_portfolio (
    portfolio_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    cash_asset TEXT NOT NULL,
    cash_balance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_position (
    portfolio_id TEXT NOT NULL REFERENCES paper_portfolio(portfolio_id),
    asset TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, asset)
);

CREATE TABLE IF NOT EXISTS paper_execution (
    order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL REFERENCES paper_portfolio(portfolio_id),
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    execution_model_version TEXT NOT NULL DEFAULT 'paper-fill-v1',
    fee_rate TEXT NOT NULL DEFAULT '0.0005',
    slippage_rate TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS paper_rebalance_decision (
    decision_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES paper_portfolio(portfolio_id),
    strategy_version TEXT NOT NULL,
    as_of TEXT NOT NULL,
    universe_observed_at TEXT NOT NULL,
    execute INTEGER NOT NULL,
    equity TEXT NOT NULL,
    assessments_json TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    orders_json TEXT NOT NULL,
    risk_violations_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    market_context_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS observation_experiment (
    experiment_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    planned_end_at TEXT NOT NULL,
    status TEXT NOT NULL,
    starting_equity DOUBLE PRECISION NOT NULL,
    completed_at TEXT,
    interruption_reason TEXT
);

CREATE TABLE IF NOT EXISTS decision_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES observation_experiment(experiment_id),
    decision_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    asset TEXT NOT NULL,
    market TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    score DOUBLE PRECISION,
    rank INTEGER,
    eligible INTEGER NOT NULL,
    selected INTEGER NOT NULL,
    current_position DOUBLE PRECISION NOT NULL,
    target_position DOUBLE PRECISION NOT NULL,
    portfolio_cash DOUBLE PRECISION NOT NULL,
    portfolio_equity DOUBLE PRECISION NOT NULL,
    current_exposure DOUBLE PRECISION NOT NULL,
    target_exposure DOUBLE PRECISION NOT NULL,
    reference_price DOUBLE PRECISION,
    liquidity DOUBLE PRECISION,
    hour_of_day INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,
    momentum_1h DOUBLE PRECISION,
    momentum_4h DOUBLE PRECISION,
    momentum_24h DOUBLE PRECISION,
    volatility DOUBLE PRECISION,
    reference_at TEXT,
    selected_rank INTEGER,
    raw_score DOUBLE PRECISION,
    score_penalty DOUBLE PRECISION,
    expected_relative_return_1h DOUBLE PRECISION,
    expected_relative_return_4h DOUBLE PRECISION,
    fee_adjusted_expected_return DOUBLE PRECISION,
    candidate_reasons_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (experiment_id, decision_id, asset)
);

CREATE TABLE IF NOT EXISTS decision_outcome (
    snapshot_id TEXT NOT NULL REFERENCES decision_snapshot(snapshot_id),
    horizon_hours INTEGER NOT NULL,
    target_at TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    forward_return DOUBLE PRECISION,
    mfe DOUBLE PRECISION,
    mae DOUBLE PRECISION,
    PRIMARY KEY (snapshot_id, horizon_hours)
);

CREATE TABLE IF NOT EXISTS decision_outcome_minute (
    snapshot_id TEXT NOT NULL REFERENCES decision_snapshot(snapshot_id),
    horizon_minutes INTEGER NOT NULL,
    target_at TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    forward_return DOUBLE PRECISION,
    mfe DOUBLE PRECISION,
    mae DOUBLE PRECISION,
    missing_reason TEXT,
    PRIMARY KEY (snapshot_id, horizon_minutes)
);

CREATE TABLE IF NOT EXISTS decision_market_context (
    experiment_id TEXT NOT NULL REFERENCES observation_experiment(experiment_id),
    decision_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    context_json TEXT NOT NULL,
    PRIMARY KEY (experiment_id, decision_id)
);

CREATE TABLE IF NOT EXISTS decision_selection_variant (
    snapshot_id TEXT NOT NULL REFERENCES decision_snapshot(snapshot_id),
    variant_id TEXT NOT NULL,
    selected INTEGER NOT NULL,
    target_position DOUBLE PRECISION NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, variant_id)
);

CREATE TABLE IF NOT EXISTS derivatives_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    index_price TEXT NOT NULL,
    open_interest_usd TEXT NOT NULL,
    funding_rate TEXT NOT NULL,
    basis_rate TEXT NOT NULL,
    global_long_short_ratio TEXT NOT NULL,
    top_position_long_short_ratio TEXT NOT NULL,
    taker_buy_sell_ratio TEXT NOT NULL,
    source TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    coinbase_price_usd TEXT,
    coinbase_premium_rate TEXT,
    coinbase_observed_at TEXT
);

CREATE TABLE IF NOT EXISTS mark_price_observation (
    symbol TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    index_price TEXT NOT NULL,
    funding_rate TEXT NOT NULL,
    next_funding_at TEXT NOT NULL,
    PRIMARY KEY (symbol, event_at)
);

CREATE TABLE IF NOT EXISTS squeeze_signal (
    snapshot_id TEXT NOT NULL REFERENCES derivatives_snapshot(snapshot_id),
    feature_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    state TEXT NOT NULL,
    fuel_score DOUBLE PRECISION,
    ignition_score DOUBLE PRECISION,
    open_interest_change_15m DOUBLE PRECISION,
    open_interest_change_1h DOUBLE PRECISION,
    futures_price_change_15m DOUBLE PRECISION,
    futures_price_change_1h DOUBLE PRECISION,
    spot_return_15m DOUBLE PRECISION,
    spot_volume_ratio DOUBLE PRECISION,
    spot_breakout INTEGER,
    short_liquidation_usd_15m DOUBLE PRECISION,
    liquidation_confirmed INTEGER NOT NULL,
    basis_input_rate TEXT,
    basis_input_source TEXT,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, feature_version)
);

CREATE TABLE IF NOT EXISTS crowding_signal (
    snapshot_id TEXT NOT NULL REFERENCES derivatives_snapshot(snapshot_id),
    feature_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    state TEXT NOT NULL,
    dominant_side TEXT NOT NULL,
    long_crowding_score DOUBLE PRECISION,
    short_crowding_score DOUBLE PRECISION,
    crowding_intensity DOUBLE PRECISION,
    bullish_unwind_score DOUBLE PRECISION,
    bearish_unwind_score DOUBLE PRECISION,
    confidence DOUBLE PRECISION NOT NULL,
    long_liquidation_usd_15m DOUBLE PRECISION,
    short_liquidation_usd_15m DOUBLE PRECISION,
    liquidation_confirmed INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, feature_version)
);

CREATE TABLE IF NOT EXISTS liquidation_event (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    event_time TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    position TEXT NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    notional_usd TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coinbase_price_observation (
    observation_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    source_sequence BIGINT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_stream_status (
    stream_name TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    connected_since TEXT,
    last_message_at TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_decision_portfolio_time
    ON paper_rebalance_decision(portfolio_id, as_of);
CREATE INDEX IF NOT EXISTS idx_snapshot_experiment_time
    ON decision_snapshot(experiment_id, decision_time);
CREATE INDEX IF NOT EXISTS idx_selection_variant_lookup
    ON decision_selection_variant(variant_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_derivatives_snapshot_symbol_available
    ON derivatives_snapshot(symbol, available_at);
CREATE INDEX IF NOT EXISTS idx_squeeze_signal_symbol_as_of
    ON squeeze_signal(symbol, as_of);
CREATE INDEX IF NOT EXISTS idx_crowding_signal_symbol_as_of
    ON crowding_signal(symbol, as_of);
CREATE INDEX IF NOT EXISTS idx_liquidation_symbol_event_time
    ON liquidation_event(symbol, event_time);
CREATE INDEX IF NOT EXISTS idx_coinbase_product_observed
    ON coinbase_price_observation(product_id, observed_at);

-- Shared group role for analysts, notebooks, MCP servers, and other agents.
-- Create individual LOGIN roles separately and grant this role to them; no
-- shared password is embedded in the repository.
DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'investment_readonly') THEN
        CREATE ROLE investment_readonly NOLOGIN;
    END IF;
END
$role$;

GRANT USAGE ON SCHEMA {schema} TO investment_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO investment_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema}
    GRANT SELECT ON TABLES TO investment_readonly;
