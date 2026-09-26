"""Dynamic-Universe selection and safe Paper rebalance planning/execution."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import psycopg

from investment.core.domain.observation import require_utc
from investment.crypto.derivatives.domain import (
    BtcDerivativesDecisionOverlay,
    CrowdingState,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
)
from investment.crypto.derivatives.overlay import BtcDerivativesOverlayProvider
from investment.crypto.domain.accounting import (
    PaperPortfolioSnapshot,
    PaperRebalanceDecisionRecord,
)
from investment.crypto.domain.market import MarketCandle, TradingUniverse
from investment.crypto.domain.order import OrderIntent, OrderSide
from investment.crypto.domain.universe import UniverseHistory, UniverseSnapshot
from investment.crypto.ports.accounting import PaperPortfolioRepository
from investment.crypto.ports.exchange import PaperExchangeGatewayFactory
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy


@dataclass(frozen=True, slots=True)
class DynamicUniversePolicy:
    strategy_version: str = "dynamic-intraday-v2.1"
    minimum_history_bars: int = 7 * 24 * 4
    liquidity_lookback_bars: int = 24 * 4
    maximum_candidates: int = 20
    maximum_positions: int = 3
    invested_fraction: Decimal = Decimal("0.9")
    maximum_asset_weight: Decimal = Decimal("0.4")
    minimum_order_notional: Decimal = Decimal("5000")
    minimum_rebalance_fraction: Decimal = Decimal("0.02")
    exchange_fee_rate: Decimal = Decimal("0.0005")
    estimated_slippage_rate: Decimal = Decimal("0.0005")
    entry_score_hurdle: float = 0.005
    hold_score_hurdle: float = 0.001
    exit_score_hurdle: float = -0.002
    maximum_hold_rank: int = 8
    required_entry_confirmations: int = 2
    reentry_cooldown: timedelta = timedelta(hours=1)
    minimum_replacement_score_advantage: float = 0.01
    maximum_daily_turnover_fraction: Decimal = Decimal("6")
    turnover_sell_weight: Decimal = Decimal("1")
    turnover_budget_mode: str = "FIXED"
    bullish_daily_turnover_fraction: Decimal | None = None
    bullish_btc_minimum_momentum_4h: float = 0.0
    bullish_btc_minimum_momentum_24h: float = 0.0
    bullish_market_breadth_minimum: float = 1.0
    bullish_market_breadth_minimum_candidates: int = 10
    maximum_daily_fee_fraction: Decimal = Decimal("0.005")
    maximum_daily_realized_loss_fraction: Decimal = Decimal("0.02")
    scoring_method: str = "RAW_MOMENTUM"
    score_scale: str = "EXPECTED_RETURN"
    momentum_1h_weight: float = 0.45
    momentum_4h_weight: float = 0.35
    momentum_24h_weight: float = 0.20
    volatility_weight: float = -0.10
    minimum_entry_momentum_1h: float | None = None
    maximum_entry_momentum_1h: float | None = None
    minimum_entry_momentum_4h: float | None = None
    maximum_entry_momentum_4h: float | None = None
    minimum_entry_momentum_24h: float | None = None
    maximum_entry_momentum_24h: float | None = None
    maximum_entry_volatility: float | None = None
    maximum_entry_volatility_quantile: float | None = None
    minimum_hold_momentum_1h: float | None = None
    minimum_hold_momentum_24h: float | None = None
    maximum_hold_volatility: float | None = None
    maximum_holding_period: timedelta | None = None
    excluded_base_assets: tuple[str, ...] = ()
    maximum_market_data_age: timedelta | None = None
    extreme_score_penalty_threshold: float | None = None
    extreme_score_maximum_penalty: float = 0.0
    expected_return_calibration_mode: str = "DISABLED"
    expected_return_1h_intercept: float = 0.0
    expected_return_1h_score_slope: float = 0.0
    expected_return_4h_intercept: float = 0.0
    expected_return_4h_score_slope: float = 0.0
    minimum_fee_adjusted_entry_return: float = 0.0
    minimum_expected_hold_return: float = 0.0
    minimum_fee_adjusted_replacement_advantage: float = 0.0
    selection_concentration_lookback: timedelta | None = None
    maximum_selection_concentration: float | None = None
    selection_concentration_minimum_cohorts: int = 20
    rolling_asset_performance_lookback: timedelta | None = None
    rolling_asset_minimum_sells: int = 2
    maximum_rolling_asset_realized_loss_fraction: Decimal | None = None
    derivatives_overlay_mode: str = "DISABLED"
    derivatives_feature_version: str = "btc-squeeze-v1"
    derivatives_maximum_age: timedelta = timedelta(minutes=10)
    derivatives_minimum_funding_rate: Decimal | None = None
    derivatives_minimum_basis_input_rate: Decimal | None = None
    derivatives_minimum_global_long_short_ratio: Decimal | None = None
    crowding_overlay_mode: str = "DISABLED"
    crowding_feature_version: str = "btc-crowding-v1"
    crowding_maximum_age: timedelta = timedelta(minutes=10)
    crowding_maximum_long_score: float | None = None
    crowding_maximum_bearish_unwind_score: float | None = None
    maximum_entry_market_data_age: timedelta | None = None

    @property
    def round_trip_cost_hurdle(self) -> float:
        return float(Decimal("2") * (self.exchange_fee_rate + self.estimated_slippage_rate))

    @property
    def effective_entry_score_hurdle(self) -> float:
        if self.score_scale == "EXPECTED_RETURN":
            return max(self.entry_score_hurdle, self.round_trip_cost_hurdle)
        return self.entry_score_hurdle


def dynamic_policy_for_version(strategy_version: str) -> DynamicUniversePolicy:
    """Build an immutable, fingerprinted strategy profile by published version."""
    if strategy_version == "dynamic-intraday-v2.1":
        return DynamicUniversePolicy()
    if strategy_version == "dynamic-intraday-v2.2":
        return DynamicUniversePolicy(
            strategy_version=strategy_version,
            maximum_positions=2,
            invested_fraction=Decimal("0.50"),
            maximum_asset_weight=Decimal("0.25"),
            entry_score_hurdle=0.60,
            hold_score_hurdle=0.45,
            exit_score_hurdle=0.45,
            maximum_hold_rank=6,
            required_entry_confirmations=3,
            reentry_cooldown=timedelta(hours=2),
            minimum_replacement_score_advantage=0.15,
            maximum_daily_turnover_fraction=Decimal("2"),
            maximum_daily_fee_fraction=Decimal("0.003"),
            maximum_daily_realized_loss_fraction=Decimal("0.01"),
            scoring_method="CALM_PULLBACK_RANK",
            score_scale="UNIT_INTERVAL",
            momentum_1h_weight=0.45,
            momentum_4h_weight=0.20,
            momentum_24h_weight=0.10,
            volatility_weight=0.25,
            minimum_entry_momentum_1h=-0.015,
            maximum_entry_momentum_1h=0.005,
            minimum_entry_momentum_4h=-0.03,
            maximum_entry_momentum_4h=0.015,
            minimum_entry_momentum_24h=-0.08,
            maximum_entry_momentum_24h=0.08,
            maximum_entry_volatility=0.012,
            maximum_entry_volatility_quantile=0.80,
            minimum_hold_momentum_1h=-0.04,
            minimum_hold_momentum_24h=-0.08,
            maximum_hold_volatility=0.012,
            maximum_holding_period=timedelta(hours=4),
            excluded_base_assets=(
                "DAI",
                "RLUSD",
                "USD1",
                "USDC",
                "USDE",
                "USDG",
                "USDS",
                "USDT",
            ),
        )
    if strategy_version == "dynamic-intraday-v2.3":
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.2"),
            strategy_version=strategy_version,
            turnover_sell_weight=Decimal("0.50"),
            turnover_budget_mode="BULLISH_REGIME",
            bullish_daily_turnover_fraction=Decimal("3"),
            bullish_btc_minimum_momentum_4h=0.01,
            bullish_btc_minimum_momentum_24h=0.03,
            bullish_market_breadth_minimum=0.65,
            bullish_market_breadth_minimum_candidates=10,
        )
    if strategy_version == "dynamic-intraday-v2.4":
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.3"),
            strategy_version=strategy_version,
            maximum_market_data_age=timedelta(minutes=30),
            extreme_score_penalty_threshold=0.80,
            extreme_score_maximum_penalty=0.25,
            expected_return_calibration_mode="SCORE_LINEAR",
            expected_return_1h_intercept=-0.005,
            expected_return_1h_score_slope=0.010,
            expected_return_4h_intercept=-0.008,
            expected_return_4h_score_slope=0.016,
            minimum_fee_adjusted_entry_return=0.0,
            minimum_expected_hold_return=0.0,
            minimum_fee_adjusted_replacement_advantage=0.001,
            selection_concentration_lookback=timedelta(hours=24),
            maximum_selection_concentration=0.25,
            selection_concentration_minimum_cohorts=20,
            rolling_asset_performance_lookback=timedelta(hours=72),
            rolling_asset_minimum_sells=2,
            maximum_rolling_asset_realized_loss_fraction=Decimal("0.005"),
            derivatives_overlay_mode="SHADOW",
            derivatives_feature_version="btc-squeeze-v2-market-streams",
            derivatives_maximum_age=timedelta(minutes=10),
        )
    if strategy_version == "dynamic-intraday-v2.5":
        # V2.5 is a deliberately narrow challenger for the two V2.4 findings:
        # a negative four-hour continuation outcome and unreliable optional
        # official Basis coverage.  It changes only the entry-level 4h trend
        # floor and the *observed* BTC feature version.  The derivative overlay
        # remains SHADOW so its predictive value is measured before it can
        # affect any asset selection.
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.4"),
            strategy_version=strategy_version,
            minimum_entry_momentum_4h=0.0,
            derivatives_feature_version="btc-squeeze-v3-mark-index-basis-proxy",
        )
    if strategy_version == "dynamic-intraday-v2.6":
        # V2.6 is an out-of-sample Shadow challenger for V2.5's short-lived
        # edge and negative 4h/12h continuation.  It restores the permissive
        # lower 4h floor, rejects high-volatility/high-4h-pump entries, and
        # applies the already-frozen V3 BTC derivatives context as a fail-closed
        # gate for new entries only.  Existing holds/exits remain fail-open.
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.5"),
            strategy_version=strategy_version,
            minimum_entry_momentum_4h=-0.03,
            maximum_entry_momentum_4h=0.005,
            maximum_entry_volatility=0.003,
            maximum_entry_volatility_quantile=0.50,
            maximum_holding_period=timedelta(hours=1),
            extreme_score_penalty_threshold=0.70,
            extreme_score_maximum_penalty=0.35,
            derivatives_overlay_mode="ENTRY_GATE",
            derivatives_minimum_funding_rate=Decimal("0.00005"),
            derivatives_minimum_basis_input_rate=Decimal("-0.0005"),
            derivatives_minimum_global_long_short_ratio=Decimal("1.0"),
        )
    if strategy_version == "dynamic-intraday-v2.7":
        # V2.7 executes in a new isolated Paper portfolio. It narrows the 1h
        # falling-knife allowance found in V2.6, lengthens post-sale cooldown,
        # retains the frozen Squeeze V3 gate, and adds a separate Crowding V1
        # downside-regime gate. Crowding never changes cross-sectional ranks.
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.6"),
            strategy_version=strategy_version,
            minimum_entry_momentum_1h=-0.007,
            reentry_cooldown=timedelta(hours=4),
            crowding_overlay_mode="ENTRY_GATE",
            crowding_feature_version="btc-crowding-v1",
            crowding_maximum_age=timedelta(minutes=10),
            crowding_maximum_long_score=0.70,
            crowding_maximum_bearish_unwind_score=0.55,
        )
    if strategy_version == "dynamic-intraday-v2.7-accuracy-v1":
        # Accuracy patch: keep the frozen V2.7 signal and production gates, but
        # reject a new entry when its completed-candle reference is more than
        # 20 minutes old. Existing holdings retain V2.7's 30-minute valuation
        # fail-safe so a delayed sync cannot silently force a liquidation.
        return replace(
            dynamic_policy_for_version("dynamic-intraday-v2.7"),
            strategy_version=strategy_version,
            maximum_entry_market_data_age=timedelta(minutes=20),
        )
    if strategy_version in V28_STRATEGY_VERSIONS:
        baseline = dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")
        if strategy_version == "dynamic-intraday-v2.8-production-control":
            return replace(baseline, strategy_version=strategy_version)
        mode = {
            "dynamic-intraday-v2.8-momentum": "SHADOW",
            "dynamic-intraday-v2.8-long-short": "LONG_SHORT_GATE",
            "dynamic-intraday-v2.8-soft-penalty": "SOFT_PENALTY",
        }[strategy_version]
        return replace(
            baseline,
            strategy_version=strategy_version,
            derivatives_overlay_mode=mode,
            derivatives_minimum_funding_rate=(
                None if mode == "SHADOW" else baseline.derivatives_minimum_funding_rate
            ),
            derivatives_minimum_basis_input_rate=(
                None if mode == "SHADOW" else baseline.derivatives_minimum_basis_input_rate
            ),
            derivatives_minimum_global_long_short_ratio=(
                None if mode == "SHADOW" else baseline.derivatives_minimum_global_long_short_ratio
            ),
            crowding_overlay_mode="SHADOW",
            crowding_maximum_long_score=None,
            crowding_maximum_bearish_unwind_score=None,
        )
    if strategy_version in V29_STRATEGY_VERSIONS:
        # V2.9 is a contemporaneous, one-change-at-a-time Paper suite derived
        # from the frozen V2.8 production control.  The challengers test the
        # three strongest postmortem findings without changing ranking,
        # execution, exits, or position sizing at the same time.
        baseline = dynamic_policy_for_version("dynamic-intraday-v2.8-production-control")
        if strategy_version == "dynamic-intraday-v2.9-production-control":
            return replace(baseline, strategy_version=strategy_version)
        if strategy_version == "dynamic-intraday-v2.9-volatility-relaxed":
            return replace(
                baseline,
                strategy_version=strategy_version,
                maximum_entry_volatility=0.018,
                maximum_entry_volatility_quantile=None,
            )
        if strategy_version == "dynamic-intraday-v2.9-pullback-window":
            return replace(
                baseline,
                strategy_version=strategy_version,
                minimum_entry_momentum_1h=-0.03,
                maximum_entry_momentum_1h=-0.007,
                minimum_entry_momentum_4h=-0.03,
                maximum_entry_momentum_4h=-0.008,
            )
        if strategy_version == "dynamic-intraday-v2.9-concentration-relaxed":
            return replace(
                baseline,
                strategy_version=strategy_version,
                maximum_selection_concentration=0.50,
            )
        raise AssertionError("unreachable V2.9 strategy version")
    raise ValueError(f"unsupported dynamic strategy version: {strategy_version}")


V28_STRATEGY_VERSIONS = (
    "dynamic-intraday-v2.8-production-control",
    "dynamic-intraday-v2.8-momentum",
    "dynamic-intraday-v2.8-long-short",
    "dynamic-intraday-v2.8-soft-penalty",
)
V29_STRATEGY_VERSIONS = (
    "dynamic-intraday-v2.9-production-control",
    "dynamic-intraday-v2.9-volatility-relaxed",
    "dynamic-intraday-v2.9-pullback-window",
    "dynamic-intraday-v2.9-concentration-relaxed",
)
# Frozen experimental cost surcharge, not a fitted expected return.
V28_DERIVATIVES_PENALTY_PER_BREACH = 0.0005


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    pair: str
    eligible: bool
    reason: str
    score: float | None
    average_quote_volume: Decimal | None
    latest_price: Decimal | None
    momentum_1h: float | None = None
    momentum_4h: float | None = None
    momentum_24h: float | None = None
    volatility: float | None = None
    reference_at: datetime | None = None
    raw_score: float | None = None
    score_penalty: float | None = None
    expected_relative_return_1h: float | None = None
    expected_relative_return_4h: float | None = None
    fee_adjusted_expected_return: float | None = None
    decision_reasons: tuple[str, ...] = ()
    reference_age_seconds: float | None = None
    candidate_confirmation_count: int = 0
    entry_signal_eligible: bool = False
    entry_eligible_confirmation_count: int = 0


@dataclass(frozen=True, slots=True)
class SelectedAsset:
    pair: str
    score: float
    target_weight: Decimal
    reason: str


V24_RULE_CONTROL_VARIANT_ID = "v2.4-rule-control"
V25_RULE_CONTROL_VARIANT_ID = "v2.5-rule-control"
V26_RULE_CONTROL_VARIANT_ID = "v2.6-rule-control"
MOMENTUM_ONLY_VARIANT_ID = "v2.7a-momentum-only"
RAW_DERIVATIVES_VARIANT_ID = "v2.7b-raw-derivatives"
CROWDING_ONLY_VARIANT_ID = "v2.7c-crowding-only"
PRODUCTION_GATE_VARIANT_ID = "v2.7d-production-gates"


@dataclass(frozen=True, slots=True)
class SelectionVariantResult:
    """A non-executing selection result evaluated from one frozen decision input."""

    variant_id: str
    selected: tuple[SelectedAsset, ...]


@dataclass(frozen=True, slots=True)
class RebalanceOrderPlan:
    intent_id: str
    pair: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    notional: Decimal
    current_weight: Decimal
    target_weight: Decimal
    status: str


@dataclass(frozen=True, slots=True)
class DynamicPaperRebalanceCommand:
    portfolio_id: str
    as_of: datetime
    execute: bool = False
    persist_decision: bool = True
    execution_deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class DynamicPaperRebalanceResult:
    portfolio_id: str
    as_of: datetime
    universe_observed_at: datetime
    dry_run: bool
    equity: Decimal
    selected: tuple[SelectedAsset, ...]
    assessments: tuple[CandidateAssessment, ...]
    orders: tuple[RebalanceOrderPlan, ...]
    final_portfolio: PaperPortfolioSnapshot
    risk_violations: tuple[str, ...] = ()
    decision_reasons: tuple[str, ...] = ()
    derivatives_overlay: BtcDerivativesDecisionOverlay | None = None
    selection_variants: tuple[SelectionVariantResult, ...] = ()
    decision_diagnostics: dict[str, object] | None = None
    execution_model_version: str = "paper-fill-v1"
    execution_fee_rate: Decimal = Decimal("0.0005")
    execution_slippage_rate: Decimal = Decimal("0")


class DynamicPaperRebalanceService:
    def __init__(
        self,
        history: UniverseHistory,
        market_data: CryptoMarketDataProvider,
        repository: PaperPortfolioRepository,
        gateway_factory: PaperExchangeGatewayFactory,
        policy: DynamicUniversePolicy | None = None,
        derivatives_overlay_provider: BtcDerivativesOverlayProvider | None = None,
    ) -> None:
        self._history = history
        self._market_data = market_data
        self._repository = repository
        self._gateway_factory = gateway_factory
        self.policy = policy or DynamicUniversePolicy()
        self._derivatives_overlay_provider = derivatives_overlay_provider

    def run(self, command: DynamicPaperRebalanceCommand) -> DynamicPaperRebalanceResult:
        as_of = require_utc(command.as_of, "as_of")
        derivatives_overlay = self._derivatives_overlay(as_of)
        snapshot = self._history.known_at(as_of)
        if snapshot is None:
            raise ValueError("no universe snapshot was known at the rebalance time")
        portfolio = self._repository.get(command.portfolio_id)
        held_pairs = {
            f"{position.asset.symbol}{portfolio.cash_asset.symbol}"
            for position in portfolio.positions
        }
        candidates, candles = self._assess(snapshot, as_of, held_pairs)
        penalty = self._derivatives_cost_penalty(derivatives_overlay)
        if penalty:
            candidates = tuple(
                replace(
                    item,
                    fee_adjusted_expected_return=(
                        item.fee_adjusted_expected_return - penalty
                        if item.fee_adjusted_expected_return is not None
                        else None
                    ),
                )
                for item in candidates
            )
        prices = {symbol: values[-1].close for symbol, values in candles.items() if values}
        missing_held = held_pairs.difference(prices)
        if missing_held:
            raise ValueError(f"cannot value held assets without 15m data: {sorted(missing_held)}")
        equity = portfolio.cash_balance + sum(
            (
                position.quantity * prices[f"{position.asset.symbol}{portfolio.cash_asset.symbol}"]
                for position in portfolio.positions
            ),
            Decimal("0"),
        )
        previous_scores = self._previous_scores(command.portfolio_id)
        confirmation_counts = self._prior_confirmation_counts(command.portfolio_id)
        entry_confirmation_counts = self._prior_entry_eligible_confirmation_counts(
            command.portfolio_id
        )
        recent_exits = self._recent_exits(command.portfolio_id, as_of)
        expired_holdings = self._expired_holdings(command.portfolio_id, held_pairs, as_of)
        concentration_blocked = self._selection_concentration_blocked(command.portfolio_id, as_of)
        rolling_loss_blocked = self._rolling_loss_blocked(command.portfolio_id, equity, as_of)
        derivatives_entry_block_reason = self._derivatives_entry_block_reason(derivatives_overlay)
        selected, selection_reasons, candidate_reasons = self._select_detailed(
            candidates,
            held_pairs,
            previous_scores,
            recent_exits,
            expired_holdings,
            confirmation_counts,
            concentration_blocked,
            rolling_loss_blocked,
            derivatives_entry_block_reason,
        )
        entry_signal_eligible = self._entry_signal_eligibility(
            candidates, derivatives_entry_block_reason
        )
        selection_variants = self._selection_variants(
            candidates,
            held_pairs,
            previous_scores,
            recent_exits,
            expired_holdings,
            confirmation_counts,
            concentration_blocked,
            rolling_loss_blocked,
            derivatives_overlay,
        )
        candidates = tuple(
            replace(
                item,
                decision_reasons=candidate_reasons.get(item.pair, ()),
                candidate_confirmation_count=(
                    confirmation_counts.get(item.pair, 0) + 1
                    if item.score is not None
                    and item.score > self.policy.effective_entry_score_hurdle
                    else 0
                ),
                entry_signal_eligible=item.pair in entry_signal_eligible,
                entry_eligible_confirmation_count=(
                    entry_confirmation_counts.get(item.pair, 0) + 1
                    if item.pair in entry_signal_eligible
                    else 0
                ),
            )
            for item in candidates
        )
        target_weights = {item.pair: item.target_weight for item in selected}
        turnover_fraction, turnover_reasons = self._turnover_budget(candidates)
        risk_violations = self._daily_risk_violations(
            portfolio.portfolio_id,
            equity,
            as_of,
            turnover_fraction,
        )
        plans, intents = self._plan(
            snapshot,
            portfolio,
            equity,
            prices,
            target_weights,
            as_of,
            block_buys=bool(risk_violations),
            blocked_buy_pairs=rolling_loss_blocked,
        )
        decision_reasons = self._decision_reasons(
            candidates,
            selected,
            plans,
            held_pairs,
            previous_scores,
            risk_violations,
            selection_reasons,
            confirmation_counts,
            turnover_reasons,
        )
        decision_reasons = tuple(
            dict.fromkeys(
                (*decision_reasons, *self._derivatives_overlay_reasons(derivatives_overlay))
            )
        )
        if command.execute:
            if command.execution_deadline is not None and datetime.now(UTC) >= require_utc(
                command.execution_deadline, "execution_deadline"
            ):
                raise ValueError("paper experiment deadline elapsed before execution")
            self._execute(intents, prices, equity)
            final = self._repository.get(command.portfolio_id)
            plans = tuple(
                RebalanceOrderPlan(
                    item.intent_id,
                    item.pair,
                    item.side,
                    item.quantity,
                    item.reference_price,
                    item.notional,
                    item.current_weight,
                    item.target_weight,
                    "PAPER_FILLED",
                )
                for item in plans
            )
        else:
            final = portfolio
        result = DynamicPaperRebalanceResult(
            portfolio.portfolio_id,
            as_of,
            snapshot.observed_at,
            not command.execute,
            equity,
            selected,
            candidates,
            plans,
            final,
            risk_violations,
            decision_reasons,
            derivatives_overlay,
            selection_variants,
            self._decision_diagnostics(derivatives_overlay),
            getattr(self._gateway_factory, "execution_model_version", "paper-fill-v1"),
            getattr(self._gateway_factory, "fee_rate", self.policy.exchange_fee_rate),
            getattr(self._gateway_factory, "slippage_rate", Decimal("0")),
        )
        if command.persist_decision:
            self._save_decision(result, command.execute)
        return result

    def _derivatives_overlay(self, as_of: datetime) -> BtcDerivativesDecisionOverlay | None:
        if self.policy.derivatives_overlay_mode == "DISABLED":
            return None
        if self.policy.derivatives_overlay_mode not in {
            "SHADOW",
            "ENTRY_GATE",
            "LONG_SHORT_GATE",
            "SOFT_PENALTY",
        }:
            raise ValueError(
                f"unsupported derivatives overlay mode: {self.policy.derivatives_overlay_mode}"
            )
        if self._derivatives_overlay_provider is None:
            return BtcDerivativesDecisionOverlay(
                decision_as_of=as_of,
                availability=DerivativesOverlayAvailability.MISSING,
                recommendation=DerivativesOverlayRecommendation.UNKNOWN,
                reason_codes=("DERIVATIVES_OVERLAY_PROVIDER_DISABLED",),
                feature_version=self.policy.derivatives_feature_version,
            )
        try:
            if self.policy.crowding_overlay_mode == "DISABLED":
                return self._derivatives_overlay_provider.known_at(
                    as_of,
                    feature_version=self.policy.derivatives_feature_version,
                    maximum_age=self.policy.derivatives_maximum_age,
                )
            return self._derivatives_overlay_provider.known_at(
                as_of,
                feature_version=self.policy.derivatives_feature_version,
                maximum_age=self.policy.derivatives_maximum_age,
                crowding_feature_version=self.policy.crowding_feature_version,
                crowding_maximum_age=self.policy.crowding_maximum_age,
            )
        except (KeyError, OSError, ValueError, sqlite3.Error, psycopg.Error):
            # A local read failure remains explicit. SHADOW keeps the spot selection;
            # ENTRY_GATE later fails closed for entrants from this MISSING overlay.
            return BtcDerivativesDecisionOverlay(
                decision_as_of=as_of,
                availability=DerivativesOverlayAvailability.MISSING,
                recommendation=DerivativesOverlayRecommendation.UNKNOWN,
                reason_codes=("DERIVATIVES_OVERLAY_READ_FAILED",),
                feature_version=self.policy.derivatives_feature_version,
            )

    def _derivatives_overlay_reasons(
        self, overlay: BtcDerivativesDecisionOverlay | None
    ) -> tuple[str, ...]:
        if overlay is None:
            return ()
        mode = self.policy.derivatives_overlay_mode
        return (f"DERIVATIVES_{mode}_{overlay.availability.value}_{overlay.recommendation.value}",)

    def _derivatives_entry_block_reason(
        self,
        overlay: BtcDerivativesDecisionOverlay | None,
    ) -> str | None:
        """Return a V2.6 new-entry-only BTC regime rejection.

        Missing/stale/incomplete point-in-time evidence fails closed for entrants,
        while callers deliberately do not apply this result to retained holdings or
        exits.  This avoids turning a derivatives outage into a forced liquidation.
        """

        raw_reason = self._raw_derivatives_entry_block_reason(overlay)
        if raw_reason is not None:
            return raw_reason
        return self._crowding_entry_block_reason(overlay)

    def _raw_derivatives_entry_block_reason(
        self,
        overlay: BtcDerivativesDecisionOverlay | None,
    ) -> str | None:
        mode = self.policy.derivatives_overlay_mode
        if mode not in {"ENTRY_GATE", "LONG_SHORT_GATE", "SOFT_PENALTY"}:
            return None
        if overlay is None or overlay.availability is not DerivativesOverlayAvailability.AVAILABLE:
            return "NEW_ENTRY_BLOCKED_BY_DERIVATIVES_DATA_UNAVAILABLE"
        if mode == "SOFT_PENALTY":
            if any(
                value is None
                for value in (
                    overlay.funding_rate,
                    overlay.basis_input_rate,
                    overlay.global_long_short_ratio,
                )
            ):
                return "NEW_ENTRY_BLOCKED_BY_DERIVATIVES_DATA_UNAVAILABLE"
            return None
        checks = (
            (
                overlay.funding_rate,
                self.policy.derivatives_minimum_funding_rate,
                "NEW_ENTRY_BLOCKED_BY_LOW_BTC_FUNDING_REGIME",
            ),
            (
                overlay.basis_input_rate,
                self.policy.derivatives_minimum_basis_input_rate,
                "NEW_ENTRY_BLOCKED_BY_NEGATIVE_BTC_BASIS_REGIME",
            ),
            (
                overlay.global_long_short_ratio,
                self.policy.derivatives_minimum_global_long_short_ratio,
                "NEW_ENTRY_BLOCKED_BY_LOW_BTC_LONG_SHORT_REGIME",
            ),
        )
        for actual, minimum, reason in checks:
            if (
                mode == "LONG_SHORT_GATE"
                and reason != "NEW_ENTRY_BLOCKED_BY_LOW_BTC_LONG_SHORT_REGIME"
            ):
                continue
            if minimum is not None and (actual is None or actual < minimum):
                return reason
        return None

    def _derivatives_cost_penalty(self, overlay: BtcDerivativesDecisionOverlay | None) -> float:
        if self.policy.derivatives_overlay_mode != "SOFT_PENALTY" or overlay is None:
            return 0.0
        if overlay.availability is not DerivativesOverlayAvailability.AVAILABLE:
            return 0.0  # Missing evidence is blocked separately, never scored as neutral.
        pairs = (
            (overlay.funding_rate, self.policy.derivatives_minimum_funding_rate),
            (overlay.basis_input_rate, self.policy.derivatives_minimum_basis_input_rate),
            (
                overlay.global_long_short_ratio,
                self.policy.derivatives_minimum_global_long_short_ratio,
            ),
        )
        return V28_DERIVATIVES_PENALTY_PER_BREACH * sum(
            actual is not None and minimum is not None and actual < minimum
            for actual, minimum in pairs
        )

    def _decision_diagnostics(
        self, overlay: BtcDerivativesDecisionOverlay | None
    ) -> dict[str, object]:
        current_reason = self._crowding_entry_block_reason(overlay)
        long_score = None if overlay is None else overlay.long_crowding_score
        bearish_score = None if overlay is None else overlay.bearish_unwind_score
        policy_blocked = bool(
            long_score is not None
            and bearish_score is not None
            and self.policy.crowding_maximum_long_score is not None
            and self.policy.crowding_maximum_bearish_unwind_score is not None
            and long_score >= self.policy.crowding_maximum_long_score
            and bearish_score >= self.policy.crowding_maximum_bearish_unwind_score
        )
        current_blocked = current_reason is not None
        return {
            "derivativesCostPenalty": self._derivatives_cost_penalty(overlay),
            "derivativesOverlayMode": self.policy.derivatives_overlay_mode,
            "expectedReturnCalibrationIsEmpirical": False,
            "candidateConfirmationMeaning": "CONSECUTIVE_SCORE_ABOVE_HURDLE",
            "entryEligibleConfirmationMeaning": (
                "SHADOW_SCORE_GUARDS_COST_DERIVATIVES_AND_CROWDING"
            ),
            "currentCrowdingGate": {
                "blocked": current_blocked,
                "reason": current_reason,
            },
            "policyCrowdingGate": {
                "blocked": policy_blocked,
                "minimumLongCrowdingScore": self.policy.crowding_maximum_long_score,
                "minimumBearishUnwindScore": (self.policy.crowding_maximum_bearish_unwind_score),
            },
            "crowdingGateDisagreement": current_blocked != policy_blocked,
            "squeezeRole": (
                "BTC_DERIVATIVES_STRUCTURE_DIAGNOSTIC_AND_POINT_IN_TIME_FEATURE_PROVIDER"
            ),
            "rawDerivativesGateRole": "FUNDING_BASIS_GLOBAL_LONG_SHORT_RISK_ON_GATE",
        }

    def _crowding_entry_block_reason(
        self, overlay: BtcDerivativesDecisionOverlay | None
    ) -> str | None:
        if self.policy.crowding_overlay_mode != "ENTRY_GATE":
            return None
        if (
            overlay is None
            or overlay.crowding_availability is not DerivativesOverlayAvailability.AVAILABLE
        ):
            return "NEW_ENTRY_BLOCKED_BY_CROWDING_DATA_UNAVAILABLE"
        long_score = overlay.long_crowding_score
        bearish_score = overlay.bearish_unwind_score
        if long_score is None or bearish_score is None:
            return "NEW_ENTRY_BLOCKED_BY_CROWDING_DATA_UNAVAILABLE"
        maximum_long = self.policy.crowding_maximum_long_score
        maximum_bearish = self.policy.crowding_maximum_bearish_unwind_score
        if overlay.crowding_state is CrowdingState.LONG_UNWIND:
            return "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_UNWIND"
        if (
            maximum_long is not None
            and maximum_bearish is not None
            and long_score >= maximum_long
            and bearish_score >= maximum_bearish
        ):
            return "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_AND_BEARISH_TRIGGER"
        return None

    def _assess(
        self,
        snapshot: UniverseSnapshot,
        as_of: datetime,
        held_pairs: set[str],
    ) -> tuple[tuple[CandidateAssessment, ...], dict[str, tuple[MarketCandle, ...]]]:
        assessments = []
        candles_by_symbol = {}
        start = min(
            as_of - timedelta(minutes=15 * (self.policy.minimum_history_bars + 8)),
            as_of - timedelta(days=10),
        )
        for member in snapshot.members:
            pair = member.pair
            if pair.quote.symbol != "KRW":
                continue
            if (
                pair.base.symbol in self.policy.excluded_base_assets
                and pair.symbol not in held_pairs
            ):
                assessments.append(
                    CandidateAssessment(
                        pair.symbol,
                        False,
                        "DIRECTIONAL_ASSET_EXCLUDED",
                        None,
                        None,
                        None,
                    )
                )
                continue
            if member.warning and pair.symbol not in held_pairs:
                assessments.append(
                    CandidateAssessment(pair.symbol, False, "MARKET_WARNING", None, None, None)
                )
                continue
            try:
                bundle = self._market_data.fetch(TradingUniverse((pair,)), start, as_of)
            except FileNotFoundError:
                assessments.append(
                    CandidateAssessment(pair.symbol, False, "DATA_UNAVAILABLE", None, None, None)
                )
                continue
            known = tuple(
                candle for candle in bundle.candles[pair.symbol] if candle.available_at <= as_of
            )
            maximum_age = (
                self.policy.maximum_market_data_age
                if pair.symbol in held_pairs
                else self.policy.maximum_entry_market_data_age
                or self.policy.maximum_market_data_age
            )
            if maximum_age is not None and known and as_of - known[-1].available_at > maximum_age:
                if pair.symbol in held_pairs:
                    raise ValueError(
                        f"cannot value held asset {pair.symbol} with stale 15m market data"
                    )
                assessments.append(
                    CandidateAssessment(
                        pair.symbol,
                        False,
                        "STALE_MARKET_DATA",
                        None,
                        None,
                        None,
                        reference_at=known[-1].available_at,
                        reference_age_seconds=(as_of - known[-1].available_at).total_seconds(),
                    )
                )
                continue
            if member.warning:
                if known:
                    candles_by_symbol[pair.symbol] = known
                assessments.append(
                    CandidateAssessment(
                        pair.symbol,
                        False,
                        "MARKET_WARNING",
                        None,
                        None,
                        known[-1].close if known else None,
                        reference_at=known[-1].available_at if known else None,
                    )
                )
                continue
            if len(known) < self.policy.minimum_history_bars:
                assessments.append(
                    CandidateAssessment(
                        pair.symbol,
                        False,
                        "INSUFFICIENT_HISTORY",
                        None,
                        None,
                        known[-1].close if known else None,
                        reference_at=known[-1].available_at if known else None,
                    )
                )
                continue
            candles_by_symbol[pair.symbol] = known
            window = known[-self.policy.liquidity_lookback_bars :]
            liquidity = sum((item.close * item.volume for item in window), Decimal("0")) / Decimal(
                len(window)
            )
            closes = np.asarray([float(item.close) for item in known], dtype=np.float64)
            momentum_1h = closes[-1] / closes[-5] - 1
            momentum_4h = closes[-1] / closes[-17] - 1
            momentum_24h = closes[-1] / closes[-97] - 1
            volatility = float(np.std(np.diff(np.log(closes[-97:])), ddof=1))
            score = (
                float(
                    self.policy.momentum_1h_weight * momentum_1h
                    + self.policy.momentum_4h_weight * momentum_4h
                    + self.policy.momentum_24h_weight * momentum_24h
                    + self.policy.volatility_weight * volatility
                )
                if self.policy.scoring_method == "RAW_MOMENTUM"
                else None
            )
            assessments.append(
                CandidateAssessment(
                    pair.symbol,
                    True,
                    "ELIGIBLE",
                    score,
                    liquidity,
                    known[-1].close,
                    float(momentum_1h),
                    float(momentum_4h),
                    float(momentum_24h),
                    volatility,
                    known[-1].available_at,
                    reference_age_seconds=(as_of - known[-1].available_at).total_seconds(),
                )
            )
        return self._score_assessments(tuple(assessments)), candles_by_symbol

    def _score_assessments(
        self, assessments: tuple[CandidateAssessment, ...]
    ) -> tuple[CandidateAssessment, ...]:
        if self.policy.scoring_method == "RAW_MOMENTUM":
            return tuple(self._with_score_evidence(item, item.score) for item in assessments)
        if self.policy.scoring_method != "CALM_PULLBACK_RANK":
            raise ValueError(f"unsupported scoring method: {self.policy.scoring_method}")
        liquid = sorted(
            (
                item
                for item in assessments
                if item.eligible
                and item.momentum_1h is not None
                and item.momentum_4h is not None
                and item.momentum_24h is not None
                and item.volatility is not None
            ),
            key=lambda item: (-(item.average_quote_volume or Decimal("0")), item.pair),
        )[: self.policy.maximum_candidates]
        if not liquid:
            return assessments
        component_ranks = {
            "momentum_1h": self._percentile_ranks(liquid, "momentum_1h"),
            "momentum_4h": self._percentile_ranks(liquid, "momentum_4h"),
            "momentum_24h": self._percentile_ranks(liquid, "momentum_24h"),
            "volatility": self._percentile_ranks(liquid, "volatility"),
        }
        raw_scores = {
            item.pair: (
                self.policy.momentum_1h_weight * (1 - component_ranks["momentum_1h"][item.pair])
                + self.policy.momentum_4h_weight * (1 - component_ranks["momentum_4h"][item.pair])
                + self.policy.momentum_24h_weight * (1 - component_ranks["momentum_24h"][item.pair])
                + self.policy.volatility_weight * (1 - component_ranks["volatility"][item.pair])
            )
            for item in liquid
        }
        return tuple(
            self._with_score_evidence(item, raw_scores.get(item.pair)) for item in assessments
        )

    def _with_score_evidence(
        self,
        item: CandidateAssessment,
        raw_score: float | None,
    ) -> CandidateAssessment:
        if raw_score is None:
            return replace(item, score=None, raw_score=None, score_penalty=None)
        score = raw_score
        penalty = 0.0
        threshold = self.policy.extreme_score_penalty_threshold
        if threshold is not None and raw_score > threshold:
            normalized_excess = min(1.0, (raw_score - threshold) / (1.0 - threshold))
            penalty = self.policy.extreme_score_maximum_penalty * normalized_excess**2
            score = max(0.0, raw_score - penalty)
        expected_1h, expected_4h = self._expected_relative_returns(score)
        fee_adjusted = (
            min(expected_1h, expected_4h) - self.policy.round_trip_cost_hurdle
            if expected_1h is not None and expected_4h is not None
            else None
        )
        return replace(
            item,
            score=score,
            raw_score=raw_score,
            score_penalty=penalty,
            expected_relative_return_1h=expected_1h,
            expected_relative_return_4h=expected_4h,
            fee_adjusted_expected_return=fee_adjusted,
        )

    def _expected_relative_returns(self, score: float) -> tuple[float | None, float | None]:
        if self.policy.expected_return_calibration_mode == "DISABLED":
            return None, None
        if self.policy.expected_return_calibration_mode != "SCORE_LINEAR":
            raise ValueError(
                "unsupported expected-return calibration mode: "
                f"{self.policy.expected_return_calibration_mode}"
            )
        return (
            self.policy.expected_return_1h_intercept
            + self.policy.expected_return_1h_score_slope * score,
            self.policy.expected_return_4h_intercept
            + self.policy.expected_return_4h_score_slope * score,
        )

    @staticmethod
    def _percentile_ranks(values: list[CandidateAssessment], field: str) -> dict[str, float]:
        ordered = sorted(
            values,
            key=lambda item: (float(getattr(item, field)), item.pair),
        )
        if len(ordered) == 1:
            return {ordered[0].pair: 0.5}
        ranks: dict[str, float] = {}
        start = 0
        while start < len(ordered):
            value = getattr(ordered[start], field)
            end = start + 1
            while end < len(ordered) and getattr(ordered[end], field) == value:
                end += 1
            average_index = (start + end - 1) / 2
            for index in range(start, end):
                ranks[ordered[index].pair] = average_index / (len(ordered) - 1)
            start = end
        return ranks

    def _selection_variants(
        self,
        assessments: tuple[CandidateAssessment, ...],
        held_pairs: set[str],
        previous_scores: dict[str, float],
        recent_exits: set[str],
        expired_holdings: set[str],
        confirmation_counts: dict[str, int],
        concentration_blocked: set[str],
        rolling_loss_blocked: set[str],
        derivatives_overlay: BtcDerivativesDecisionOverlay | None = None,
    ) -> tuple[SelectionVariantResult, ...]:
        """Return the version-bound, non-executing same-input rule control."""
        if self.policy.strategy_version == "dynamic-intraday-v2.7-accuracy-v1":
            variant_reasons = (
                (MOMENTUM_ONLY_VARIANT_ID, None),
                (
                    RAW_DERIVATIVES_VARIANT_ID,
                    self._raw_derivatives_entry_block_reason(derivatives_overlay),
                ),
                (
                    CROWDING_ONLY_VARIANT_ID,
                    self._crowding_entry_block_reason(derivatives_overlay),
                ),
                (
                    PRODUCTION_GATE_VARIANT_ID,
                    self._derivatives_entry_block_reason(derivatives_overlay),
                ),
            )
            results = []
            for variant_id, block_reason in variant_reasons:
                selected, _, _ = self._select_detailed(
                    assessments,
                    held_pairs,
                    previous_scores,
                    recent_exits,
                    expired_holdings,
                    confirmation_counts,
                    concentration_blocked,
                    rolling_loss_blocked,
                    block_reason,
                )
                results.append(SelectionVariantResult(variant_id, selected))
            return tuple(results)
        if self.policy.strategy_version not in {
            "dynamic-intraday-v2.5",
            "dynamic-intraday-v2.6",
            "dynamic-intraday-v2.7",
        }:
            return ()
        if self.policy.strategy_version == "dynamic-intraday-v2.5":
            variant_id = V24_RULE_CONTROL_VARIANT_ID
            control_policy = replace(self.policy, minimum_entry_momentum_4h=-0.03)
        elif self.policy.strategy_version == "dynamic-intraday-v2.6":
            variant_id = V25_RULE_CONTROL_VARIANT_ID
            control_policy = replace(
                self.policy,
                minimum_entry_momentum_4h=0.0,
                maximum_entry_momentum_4h=0.015,
                maximum_entry_volatility=0.012,
                maximum_entry_volatility_quantile=0.80,
                maximum_holding_period=timedelta(hours=4),
                extreme_score_penalty_threshold=0.80,
                extreme_score_maximum_penalty=0.25,
                derivatives_overlay_mode="SHADOW",
                derivatives_minimum_funding_rate=None,
                derivatives_minimum_basis_input_rate=None,
                derivatives_minimum_global_long_short_ratio=None,
            )
        else:
            variant_id = V26_RULE_CONTROL_VARIANT_ID
            control_policy = dynamic_policy_for_version("dynamic-intraday-v2.6")
        control = DynamicPaperRebalanceService(
            self._history,
            self._market_data,
            self._repository,
            self._gateway_factory,
            control_policy,
            self._derivatives_overlay_provider,
        )
        # Candidate features and unpenalized rank scores are frozen. V2.6 changes
        # only the nonlinear penalty, so its V2.5 control reapplies the old
        # penalty to the recorded raw score without rebuilding the cross-section.
        control_assessments = (
            tuple(
                control._with_score_evidence(item, item.raw_score)
                if item.raw_score is not None
                else item
                for item in assessments
            )
            if self.policy.strategy_version == "dynamic-intraday-v2.6"
            else assessments
        )
        control_entry_block_reason = control._derivatives_entry_block_reason(derivatives_overlay)
        selected, _, _ = control._select_detailed(
            control_assessments,
            held_pairs,
            previous_scores,
            recent_exits,
            expired_holdings,
            confirmation_counts,
            concentration_blocked,
            rolling_loss_blocked,
            control_entry_block_reason,
        )
        return (SelectionVariantResult(variant_id, selected),)

    def _entry_signal_eligibility(
        self,
        assessments: tuple[CandidateAssessment, ...],
        entry_block_reason: str | None,
    ) -> set[str]:
        """Return current signal-valid candidates without portfolio/account constraints."""
        liquid = sorted(
            (item for item in assessments if item.eligible),
            key=lambda item: (-(item.average_quote_volume or Decimal("0")), item.pair),
        )[: self.policy.maximum_candidates]
        scored = sorted(liquid, key=lambda item: (-float(item.score or 0), item.pair))
        volatility_cap = self._entry_volatility_cap(scored)
        if entry_block_reason is not None:
            return set()
        return {
            item.pair
            for item in scored
            if item.score is not None
            and item.score > self.policy.effective_entry_score_hurdle
            and self._entry_guard_reason(item, volatility_cap) is None
            and self._fee_adjusted_entry_return_passes(item)
        }

    def _select(
        self,
        assessments: tuple[CandidateAssessment, ...],
        held_pairs: set[str],
        previous_scores: dict[str, float],
        recent_exits: set[str],
        expired_holdings: set[str] | None = None,
        confirmation_counts: dict[str, int] | None = None,
    ) -> tuple[tuple[SelectedAsset, ...], tuple[str, ...]]:
        selected, reasons, _ = self._select_detailed(
            assessments,
            held_pairs,
            previous_scores,
            recent_exits,
            expired_holdings,
            confirmation_counts,
        )
        return selected, reasons

    def _select_detailed(
        self,
        assessments: tuple[CandidateAssessment, ...],
        held_pairs: set[str],
        previous_scores: dict[str, float],
        recent_exits: set[str],
        expired_holdings: set[str] | None = None,
        confirmation_counts: dict[str, int] | None = None,
        concentration_blocked: set[str] | None = None,
        rolling_loss_blocked: set[str] | None = None,
        entry_block_reason: str | None = None,
    ) -> tuple[
        tuple[SelectedAsset, ...],
        tuple[str, ...],
        dict[str, tuple[str, ...]],
    ]:
        expired = expired_holdings or set()
        concentration_blocked = concentration_blocked or set()
        rolling_loss_blocked = rolling_loss_blocked or set()
        liquid = sorted(
            (item for item in assessments if item.eligible),
            key=lambda item: (-(item.average_quote_volume or Decimal("0")), item.pair),
        )[: self.policy.maximum_candidates]
        scored = sorted(liquid, key=lambda item: (-float(item.score or 0), item.pair))
        ranks = {item.pair: rank for rank, item in enumerate(scored, start=1)}
        entry_volatility_cap = self._entry_volatility_cap(scored)
        evidence: dict[str, list[str]] = {item.pair: [] for item in assessments}
        guard_reasons = {
            reason
            for item in scored
            if item.pair not in held_pairs
            and (reason := self._entry_guard_reason(item, entry_volatility_cap)) is not None
        } | {
            reason
            for item in scored
            if item.pair in held_pairs and (reason := self._hold_guard_reason(item)) is not None
        }
        for item in scored:
            if item.pair in held_pairs:
                if item.pair in expired:
                    evidence[item.pair].append("HOLD_EXITED_BY_MAXIMUM_HOLDING_PERIOD")
                if item.score is None or item.score <= self.policy.exit_score_hurdle:
                    evidence[item.pair].append("HOLD_SCORE_BELOW_EXIT_HURDLE")
                if ranks[item.pair] > self.policy.maximum_hold_rank:
                    evidence[item.pair].append("HOLD_RANK_BELOW_LIMIT")
                if reason := self._hold_guard_reason(item):
                    evidence[item.pair].append(reason)
                if not self._expected_hold_return_passes(item):
                    evidence[item.pair].append("HOLD_EXPECTED_RELATIVE_RETURN_INSUFFICIENT")
                continue
            if item.pair in recent_exits:
                evidence[item.pair].append("NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN")
            if item.score is None or item.score <= self.policy.effective_entry_score_hurdle:
                evidence[item.pair].append("NEW_ENTRY_SCORE_BELOW_HURDLE")
            if reason := self._entry_guard_reason(item, entry_volatility_cap):
                evidence[item.pair].append(reason)
            if not self._fee_adjusted_entry_return_passes(item):
                evidence[item.pair].append("NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT")
            if item.pair in concentration_blocked:
                evidence[item.pair].append("NEW_ENTRY_BLOCKED_BY_SELECTION_CONCENTRATION")
            if item.pair in rolling_loss_blocked:
                evidence[item.pair].append("NEW_ENTRY_BLOCKED_BY_ROLLING_ASSET_LOSS")
            if entry_block_reason is not None:
                evidence[item.pair].append(entry_block_reason)
            prior_count = (
                confirmation_counts.get(item.pair, 0)
                if confirmation_counts is not None
                else int(
                    previous_scores.get(item.pair, float("-inf"))
                    > self.policy.effective_entry_score_hurdle
                )
            )
            if prior_count < self.policy.required_entry_confirmations - 1:
                evidence[item.pair].append("NEW_ENTRY_WAITING_FOR_CONFIRMATIONS")
        retained = [
            item
            for item in scored
            if item.pair in held_pairs
            and item.pair not in expired
            and item.score is not None
            and item.score > self.policy.exit_score_hurdle
            and ranks[item.pair] <= self.policy.maximum_hold_rank
            and self._hold_guard_reason(item) is None
            and self._expected_hold_return_passes(item)
        ]
        entrants = [
            item
            for item in scored
            if item.pair not in held_pairs
            and item.pair not in recent_exits
            and item.pair not in concentration_blocked
            and item.pair not in rolling_loss_blocked
            and entry_block_reason is None
            and item.score is not None
            and item.score > self.policy.effective_entry_score_hurdle
            and self._entry_guard_reason(item, entry_volatility_cap) is None
            and self._fee_adjusted_entry_return_passes(item)
            and (
                self.policy.required_entry_confirmations <= 1
                or (
                    confirmation_counts.get(item.pair, 0)
                    if confirmation_counts is not None
                    else int(
                        previous_scores.get(item.pair, float("-inf"))
                        > self.policy.effective_entry_score_hurdle
                    )
                )
                >= self.policy.required_entry_confirmations - 1
            )
        ]
        cooldown_blocked = any(
            item.pair in recent_exits
            and item.score is not None
            and item.score > self.policy.effective_entry_score_hurdle
            for item in scored
        )
        ranked = sorted(
            retained,
            key=lambda item: (-float(item.score or 0), item.pair),
        )[: self.policy.maximum_positions]
        replacement_blocked = False
        replacement_return_blocked = False
        for entrant in entrants:
            if len(ranked) < self.policy.maximum_positions:
                ranked.append(entrant)
                continue
            weakest = min(ranked, key=lambda item: (float(item.score or 0), item.pair))
            score_passes = float(entrant.score or 0) >= (
                float(weakest.score or 0) + self.policy.minimum_replacement_score_advantage
            )
            return_passes = self._fee_adjusted_replacement_passes(entrant, weakest)
            if score_passes and return_passes:
                ranked.remove(weakest)
                ranked.append(entrant)
                evidence[entrant.pair].append("REPLACEMENT_FEE_ADJUSTED_ADVANTAGE_CONFIRMED")
                evidence[weakest.pair].append("REPLACED_BY_STRONGER_FEE_ADJUSTED_CANDIDATE")
            else:
                if not score_passes:
                    replacement_blocked = True
                    evidence[entrant.pair].append("REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT")
                if not return_passes:
                    replacement_return_blocked = True
                    evidence[entrant.pair].append("REPLACEMENT_FEE_ADJUSTED_RETURN_INSUFFICIENT")
        ranked.sort(key=lambda item: (-float(item.score or 0), item.pair))
        reasons = sorted(guard_reasons)
        if expired:
            reasons.append("HOLD_EXITED_BY_MAXIMUM_HOLDING_PERIOD")
        if cooldown_blocked:
            reasons.append("NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN")
        if replacement_blocked:
            reasons.append("REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT")
        if replacement_return_blocked:
            reasons.append("REPLACEMENT_FEE_ADJUSTED_RETURN_INSUFFICIENT")
        if concentration_blocked:
            reasons.append("NEW_ENTRY_BLOCKED_BY_SELECTION_CONCENTRATION")
        if rolling_loss_blocked:
            reasons.append("NEW_ENTRY_BLOCKED_BY_ROLLING_ASSET_LOSS")
        if entry_block_reason is not None:
            reasons.append(entry_block_reason)
        fee_blocked = any(
            "NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT" in values for values in evidence.values()
        )
        if fee_blocked:
            reasons.append("NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT")
        frozen_evidence = {
            pair: tuple(dict.fromkeys(values)) for pair, values in evidence.items() if values
        }
        if not ranked:
            return (), tuple(dict.fromkeys(reasons)), frozen_evidence
        total_invested = min(
            self.policy.invested_fraction,
            self.policy.maximum_asset_weight * Decimal(len(ranked)),
        )
        weight = total_invested / Decimal(len(ranked))
        selected = tuple(
            SelectedAsset(
                item.pair,
                item.score if item.score is not None else 0.0,
                weight,
                (
                    "HELD_WITH_POSITIVE_EXPECTED_RELATIVE_RETURN"
                    if item.pair in held_pairs
                    and self.policy.expected_return_calibration_mode != "DISABLED"
                    else "HELD_WITHIN_EXIT_HYSTERESIS"
                    if item.pair in held_pairs
                    else "ENTRY_CONFIRMED_ABOVE_FEE_ADJUSTED_HURDLE"
                    if self.policy.expected_return_calibration_mode != "DISABLED"
                    else "ENTRY_CONFIRMED_ABOVE_COST_HURDLE"
                ),
            )
            for item in ranked
        )
        return selected, tuple(dict.fromkeys(reasons)), frozen_evidence

    def _conservative_expected_relative_return(self, item: CandidateAssessment) -> float | None:
        if item.expected_relative_return_1h is None or item.expected_relative_return_4h is None:
            return None
        return min(item.expected_relative_return_1h, item.expected_relative_return_4h)

    def _fee_adjusted_entry_return_passes(self, item: CandidateAssessment) -> bool:
        if self.policy.expected_return_calibration_mode == "DISABLED":
            return True
        return bool(
            item.fee_adjusted_expected_return is not None
            and item.fee_adjusted_expected_return >= self.policy.minimum_fee_adjusted_entry_return
        )

    def _expected_hold_return_passes(self, item: CandidateAssessment) -> bool:
        if self.policy.expected_return_calibration_mode == "DISABLED":
            return True
        expected = self._conservative_expected_relative_return(item)
        # Missing model evidence must not force a liquidation of an existing holding.
        return expected is None or expected >= self.policy.minimum_expected_hold_return

    def _fee_adjusted_replacement_passes(
        self,
        entrant: CandidateAssessment,
        incumbent: CandidateAssessment,
    ) -> bool:
        if self.policy.expected_return_calibration_mode == "DISABLED":
            return True
        entrant_return = self._conservative_expected_relative_return(entrant)
        incumbent_return = self._conservative_expected_relative_return(incumbent)
        if entrant_return is None or incumbent_return is None:
            return False
        net_advantage = entrant_return - incumbent_return - self.policy.round_trip_cost_hurdle
        return net_advantage >= self.policy.minimum_fee_adjusted_replacement_advantage

    def _entry_guard_reason(
        self, item: CandidateAssessment, volatility_cap: float | None = None
    ) -> str | None:
        policy = self.policy
        if (
            policy.minimum_entry_momentum_1h is not None
            and item.momentum_1h is not None
            and item.momentum_1h < policy.minimum_entry_momentum_1h
        ):
            return "NEW_ENTRY_BLOCKED_BY_FALLING_KNIFE_GUARD"
        if (
            policy.maximum_entry_momentum_1h is not None
            and item.momentum_1h is not None
            and item.momentum_1h > policy.maximum_entry_momentum_1h
        ):
            return "NEW_ENTRY_BLOCKED_BY_SHORT_TERM_SPIKE_GUARD"
        if (
            policy.minimum_entry_momentum_4h is not None
            and item.momentum_4h is not None
            and item.momentum_4h < policy.minimum_entry_momentum_4h
        ):
            return "NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD"
        if (
            policy.maximum_entry_momentum_4h is not None
            and item.momentum_4h is not None
            and item.momentum_4h > policy.maximum_entry_momentum_4h
        ):
            return "NEW_ENTRY_BLOCKED_BY_4H_SPIKE_GUARD"
        if (
            policy.minimum_entry_momentum_24h is not None
            and item.momentum_24h is not None
            and item.momentum_24h < policy.minimum_entry_momentum_24h
        ):
            return "NEW_ENTRY_BLOCKED_BY_24H_TREND_GUARD"
        if (
            policy.maximum_entry_momentum_24h is not None
            and item.momentum_24h is not None
            and item.momentum_24h > policy.maximum_entry_momentum_24h
        ):
            return "NEW_ENTRY_BLOCKED_BY_24H_SPIKE_GUARD"
        if (
            volatility_cap is not None
            and item.volatility is not None
            and item.volatility > volatility_cap
        ):
            return "NEW_ENTRY_BLOCKED_BY_VOLATILITY_GUARD"
        return None

    def _entry_volatility_cap(self, assessments: list[CandidateAssessment]) -> float | None:
        absolute = self.policy.maximum_entry_volatility
        quantile = self.policy.maximum_entry_volatility_quantile
        values = sorted(
            float(item.volatility) for item in assessments if item.volatility is not None
        )
        if quantile is None or not values:
            return absolute
        index = min(len(values) - 1, max(0, int((len(values) - 1) * quantile)))
        relative = values[index]
        return relative if absolute is None else min(absolute, relative)

    def _hold_guard_reason(self, item: CandidateAssessment) -> str | None:
        policy = self.policy
        if (
            policy.minimum_hold_momentum_1h is not None
            and item.momentum_1h is not None
            and item.momentum_1h < policy.minimum_hold_momentum_1h
        ):
            return "HOLD_BLOCKED_BY_FALLING_KNIFE_GUARD"
        if (
            policy.minimum_hold_momentum_24h is not None
            and item.momentum_24h is not None
            and item.momentum_24h < policy.minimum_hold_momentum_24h
        ):
            return "HOLD_BLOCKED_BY_24H_TREND_GUARD"
        if (
            policy.maximum_hold_volatility is not None
            and item.volatility is not None
            and item.volatility > policy.maximum_hold_volatility
        ):
            return "HOLD_BLOCKED_BY_VOLATILITY_GUARD"
        return None

    def _recent_exits(self, portfolio_id: str, as_of: datetime) -> set[str]:
        cutoff = as_of - self.policy.reentry_cooldown
        return {
            item.pair
            for item in self._repository.list_executions(portfolio_id, 1000)
            if item.side is OrderSide.SELL and cutoff < item.executed_at <= as_of
        }

    def _selection_concentration_blocked(
        self,
        portfolio_id: str,
        as_of: datetime,
    ) -> set[str]:
        lookback = self.policy.selection_concentration_lookback
        maximum = self.policy.maximum_selection_concentration
        if lookback is None or maximum is None:
            return set()
        cutoff = as_of - lookback
        selected_cohorts: list[set[str]] = []
        for record in self._repository.list_rebalance_decisions(portfolio_id, 10_000):
            if record.strategy_version != self.policy.strategy_version:
                continue
            if not cutoff < record.as_of < as_of:
                continue
            payload = json.loads(record.selected_json)
            selected = {
                str(item["pair"])
                for item in payload
                if isinstance(item, dict) and isinstance(item.get("pair"), str)
            }
            if selected:
                selected_cohorts.append(selected)
        cohort_count = len(selected_cohorts)
        if cohort_count < self.policy.selection_concentration_minimum_cohorts:
            return set()
        pairs = {pair for cohort in selected_cohorts for pair in cohort}
        return {
            pair
            for pair in pairs
            if (sum(pair in cohort for cohort in selected_cohorts) + 1) / (cohort_count + 1)
            > maximum
        }

    def _rolling_loss_blocked(
        self,
        portfolio_id: str,
        equity: Decimal,
        as_of: datetime,
    ) -> set[str]:
        lookback = self.policy.rolling_asset_performance_lookback
        maximum_loss = self.policy.maximum_rolling_asset_realized_loss_fraction
        if lookback is None or maximum_loss is None:
            return set()
        cutoff = as_of - lookback
        sells: dict[str, list[Decimal]] = {}
        for execution in self._repository.list_executions(portfolio_id, 10_000):
            if execution.side is OrderSide.SELL and cutoff < execution.executed_at <= as_of:
                sells.setdefault(execution.pair, []).append(execution.realized_pnl)
        threshold = -(equity * maximum_loss)
        return {
            pair
            for pair, returns in sells.items()
            if len(returns) >= self.policy.rolling_asset_minimum_sells
            and sum(returns, Decimal("0")) <= threshold
        }

    def _expired_holdings(
        self, portfolio_id: str, held_pairs: set[str], as_of: datetime
    ) -> set[str]:
        maximum_holding_period = self.policy.maximum_holding_period
        if maximum_holding_period is None or not held_pairs:
            return set()
        quantities: dict[str, Decimal] = {}
        opened_at: dict[str, datetime] = {}
        executions = sorted(
            self._repository.list_executions(portfolio_id, 10_000),
            key=lambda item: item.executed_at,
        )
        for item in executions:
            current = quantities.get(item.pair, Decimal("0"))
            if item.side is OrderSide.BUY:
                if current <= 0:
                    opened_at[item.pair] = item.executed_at
                quantities[item.pair] = current + item.quantity
            else:
                remaining = max(Decimal("0"), current - item.quantity)
                quantities[item.pair] = remaining
                if remaining == 0:
                    opened_at.pop(item.pair, None)
        return {
            pair
            for pair in held_pairs
            if pair in opened_at and as_of - opened_at[pair] >= maximum_holding_period
        }

    def _plan(
        self,
        snapshot: UniverseSnapshot,
        portfolio: PaperPortfolioSnapshot,
        equity: Decimal,
        prices: dict[str, Decimal],
        targets: dict[str, Decimal],
        as_of: datetime,
        *,
        block_buys: bool = False,
        blocked_buy_pairs: set[str] | None = None,
    ) -> tuple[tuple[RebalanceOrderPlan, ...], tuple[OrderIntent, ...]]:
        current_quantities = {
            f"{item.asset.symbol}{portfolio.cash_asset.symbol}": item.quantity
            for item in portfolio.positions
        }
        symbols = set(current_quantities) | set(targets)
        rows = []
        for symbol in symbols:
            price = prices.get(symbol)
            if price is None:
                continue
            current_notional = current_quantities.get(symbol, Decimal("0")) * price
            target_notional = equity * targets.get(symbol, Decimal("0"))
            difference = target_notional - current_notional
            if abs(difference) < max(
                self.policy.minimum_order_notional,
                equity * self.policy.minimum_rebalance_fraction,
            ):
                continue
            pair = next(member.pair for member in snapshot.members if member.pair.symbol == symbol)
            side = OrderSide.BUY if difference > 0 else OrderSide.SELL
            if side is OrderSide.BUY and (block_buys or symbol in (blocked_buy_pairs or set())):
                continue
            current_quantity = current_quantities.get(symbol, Decimal("0"))
            quantity = (
                current_quantity
                if side is OrderSide.SELL and target_notional == 0
                else min(abs(difference) / price, current_quantity)
                if side is OrderSide.SELL
                else abs(difference) / price
            )
            identity = hashlib.sha256(
                f"{portfolio.portfolio_id}:{as_of.isoformat()}:{symbol}:{side.value}".encode()
            ).hexdigest()[:20]
            intent = OrderIntent(
                portfolio.portfolio_id,
                portfolio.purpose,
                pair,
                side,
                quantity,
                as_of,
                f"dynamic-rebalance-{identity}",
            )
            rows.append(
                (
                    RebalanceOrderPlan(
                        intent.intent_id,
                        symbol,
                        side,
                        quantity,
                        price,
                        abs(difference),
                        current_notional / equity,
                        targets.get(symbol, Decimal("0")),
                        "DRY_RUN",
                    ),
                    intent,
                )
            )
        rows.sort(key=lambda item: (item[1].side is OrderSide.BUY, item[1].pair.symbol))
        return tuple(item[0] for item in rows), tuple(item[1] for item in rows)

    def _execute(
        self, intents: tuple[OrderIntent, ...], prices: dict[str, Decimal], equity: Decimal
    ) -> None:
        gateway = self._gateway_factory.create(prices)
        risk = DeterministicRiskEngine(
            RiskPolicy(
                maximum_positions=self.policy.maximum_positions,
                maximum_asset_fraction=self.policy.maximum_asset_weight,
                maximum_single_order_notional=equity,
                minimum_order_notional=self.policy.minimum_order_notional,
            )
        )
        for intent in intents:
            approved = risk.approve_order(intent, prices[intent.pair.symbol])
            report = gateway.submit(approved)
            self._repository.apply_execution(approved, report)

    def _previous_scores(self, portfolio_id: str) -> dict[str, float]:
        record = next(
            (
                item
                for item in self._repository.list_rebalance_decisions(portfolio_id, 100)
                if item.strategy_version == self.policy.strategy_version
            ),
            None,
        )
        if record is None:
            return {}
        payload = json.loads(record.assessments_json)
        return {
            str(item["pair"]): float(item["score"])
            for item in payload
            if isinstance(item, dict) and item.get("score") is not None
        }

    def _prior_confirmation_counts(self, portfolio_id: str) -> dict[str, int]:
        required_prior = max(0, self.policy.required_entry_confirmations - 1)
        if required_prior == 0:
            return {}
        records = tuple(
            item
            for item in self._repository.list_rebalance_decisions(portfolio_id, 100)
            if item.strategy_version == self.policy.strategy_version
        )[:required_prior]
        counts: dict[str, int] = {}
        still_confirmed: set[str] | None = None
        for record in records:
            payload = json.loads(record.assessments_json)
            passing = {
                str(item["pair"])
                for item in payload
                if isinstance(item, dict)
                and item.get("score") is not None
                and float(item["score"]) > self.policy.effective_entry_score_hurdle
            }
            still_confirmed = passing if still_confirmed is None else still_confirmed & passing
            for pair in still_confirmed:
                counts[pair] = counts.get(pair, 0) + 1
        return counts

    def _prior_entry_eligible_confirmation_counts(self, portfolio_id: str) -> dict[str, int]:
        """Count consecutive shadow entry-signal eligibility without changing orders."""
        required_prior = max(0, self.policy.required_entry_confirmations - 1)
        if required_prior == 0:
            return {}
        records = tuple(
            item
            for item in self._repository.list_rebalance_decisions(portfolio_id, 100)
            if item.strategy_version == self.policy.strategy_version
        )[:required_prior]
        counts: dict[str, int] = {}
        still_eligible: set[str] | None = None
        for record in records:
            payload = json.loads(record.assessments_json)
            passing = {
                str(item["pair"])
                for item in payload
                if isinstance(item, dict) and item.get("entrySignalEligible") is True
            }
            still_eligible = passing if still_eligible is None else still_eligible & passing
            for pair in still_eligible:
                counts[pair] = counts.get(pair, 0) + 1
        return counts

    def _daily_risk_violations(
        self,
        portfolio_id: str,
        equity: Decimal,
        as_of: datetime,
        maximum_turnover_fraction: Decimal | None = None,
    ) -> tuple[str, ...]:
        day_start = as_of.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        executions = tuple(
            item
            for item in self._repository.list_executions(portfolio_id, 1000)
            if item.executed_at >= day_start
        )
        turnover = sum(
            (
                item.quantity
                * item.price
                * (
                    self.policy.turnover_sell_weight
                    if item.side is OrderSide.SELL
                    else Decimal("1")
                )
                for item in executions
            ),
            Decimal("0"),
        )
        fees = sum((item.fee for item in executions), Decimal("0"))
        realized = sum((item.realized_pnl for item in executions), Decimal("0"))
        violations = []
        turnover_limit = maximum_turnover_fraction or self.policy.maximum_daily_turnover_fraction
        if turnover >= equity * turnover_limit:
            violations.append("DAILY_TURNOVER_BUDGET_EXHAUSTED")
        if fees >= equity * self.policy.maximum_daily_fee_fraction:
            violations.append("DAILY_FEE_BUDGET_EXHAUSTED")
        if realized <= -(equity * self.policy.maximum_daily_realized_loss_fraction):
            violations.append("DAILY_REALIZED_LOSS_LIMIT")
        return tuple(violations)

    def _turnover_budget(
        self, assessments: tuple[CandidateAssessment, ...]
    ) -> tuple[Decimal, tuple[str, ...]]:
        base = self.policy.maximum_daily_turnover_fraction
        if self.policy.turnover_budget_mode == "FIXED":
            return base, ()
        if self.policy.turnover_budget_mode != "BULLISH_REGIME":
            raise ValueError(
                f"unsupported turnover budget mode: {self.policy.turnover_budget_mode}"
            )
        expanded = self.policy.bullish_daily_turnover_fraction
        if expanded is None or expanded <= base:
            return base, (
                "DYNAMIC_TURNOVER_BASE_REGIME_ACTIVE",
                f"DYNAMIC_TURNOVER_LIMIT_{base.normalize()}X_EQUITY",
            )
        liquid = sorted(
            (
                item
                for item in assessments
                if item.eligible
                and item.average_quote_volume is not None
                and item.momentum_4h is not None
                and item.momentum_24h is not None
            ),
            key=lambda item: (-(item.average_quote_volume or Decimal("0")), item.pair),
        )[: self.policy.maximum_candidates]
        btc = next((item for item in liquid if item.pair == "BTCKRW"), None)
        breadth = (
            sum(
                bool((item.momentum_4h or 0) > 0 and (item.momentum_24h or 0) > 0)
                for item in liquid
            )
            / len(liquid)
            if liquid
            else 0.0
        )
        bullish = (
            len(liquid) >= self.policy.bullish_market_breadth_minimum_candidates
            and btc is not None
            and (btc.momentum_4h or 0) >= self.policy.bullish_btc_minimum_momentum_4h
            and (btc.momentum_24h or 0) >= self.policy.bullish_btc_minimum_momentum_24h
            and breadth >= self.policy.bullish_market_breadth_minimum
        )
        limit = expanded if bullish else base
        regime = "BULLISH" if bullish else "BASE"
        return limit, (
            f"DYNAMIC_TURNOVER_{regime}_REGIME_ACTIVE",
            f"DYNAMIC_TURNOVER_LIMIT_{limit.normalize()}X_EQUITY",
        )

    def _save_decision(self, result: DynamicPaperRebalanceResult, execute: bool) -> None:
        decision_id = hashlib.sha256(
            f"{result.portfolio_id}:{result.as_of.isoformat()}:{self.policy.strategy_version}".encode()
        ).hexdigest()[:24]
        assessments = [
            {
                "pair": item.pair,
                "eligible": item.eligible,
                "reason": item.reason,
                "score": item.score,
                "rawScore": item.raw_score,
                "scorePenalty": item.score_penalty,
                "expectedRelativeReturn1h": item.expected_relative_return_1h,
                "expectedRelativeReturn4h": item.expected_relative_return_4h,
                "feeAdjustedExpectedReturn": item.fee_adjusted_expected_return,
                "decisionReasons": list(item.decision_reasons),
                "momentum1h": item.momentum_1h,
                "momentum4h": item.momentum_4h,
                "momentum24h": item.momentum_24h,
                "volatility": item.volatility,
                "referenceAt": (
                    item.reference_at.isoformat() if item.reference_at is not None else None
                ),
                "referenceAgeSeconds": item.reference_age_seconds,
                "candidateConfirmationCount": item.candidate_confirmation_count,
                "entrySignalEligible": item.entry_signal_eligible,
                "entryEligibleConfirmationCount": item.entry_eligible_confirmation_count,
                "averageQuoteVolume": (
                    str(item.average_quote_volume)
                    if item.average_quote_volume is not None
                    else None
                ),
                "latestPrice": str(item.latest_price) if item.latest_price is not None else None,
            }
            for item in result.assessments
        ]
        selected = [
            {
                "pair": item.pair,
                "score": item.score,
                "targetWeight": str(item.target_weight),
                "reason": item.reason,
            }
            for item in result.selected
        ]
        orders = [
            {
                "intentId": item.intent_id,
                "pair": item.pair,
                "side": item.side.value,
                "quantity": str(item.quantity),
                "notional": str(item.notional),
                "status": item.status,
            }
            for item in result.orders
        ]
        self._repository.save_rebalance_decision(
            PaperRebalanceDecisionRecord(
                decision_id,
                result.portfolio_id,
                self.policy.strategy_version,
                result.as_of,
                result.universe_observed_at,
                execute,
                result.equity,
                json.dumps(assessments, separators=(",", ":")),
                json.dumps(selected, separators=(",", ":")),
                json.dumps(orders, separators=(",", ":")),
                result.risk_violations,
                result.decision_reasons,
                "EXECUTED" if execute else "DRY_RUN",
                datetime.now(UTC),
                json.dumps(
                    {
                        **(
                            result.derivatives_overlay.to_dict()
                            if result.derivatives_overlay is not None
                            else {}
                        ),
                        "decisionDiagnostics": result.decision_diagnostics or {},
                        "executionModelVersion": result.execution_model_version,
                        "feeRate": str(result.execution_fee_rate),
                        "slippageRate": str(result.execution_slippage_rate),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )

    def _decision_reasons(
        self,
        assessments: tuple[CandidateAssessment, ...],
        selected: tuple[SelectedAsset, ...],
        plans: tuple[RebalanceOrderPlan, ...],
        held_pairs: set[str],
        previous_scores: dict[str, float],
        risk_violations: tuple[str, ...],
        selection_reasons: tuple[str, ...],
        confirmation_counts: dict[str, int] | None = None,
        turnover_reasons: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        if plans:
            return tuple(
                dict.fromkeys((*turnover_reasons, "ORDERS_CREATED_FOR_TARGET_WEIGHT_CHANGES"))
            )
        reasons = [*selection_reasons, *turnover_reasons]
        if risk_violations:
            reasons.append("NEW_BUYS_BLOCKED_BY_DAILY_RISK_BUDGET")
        if selected and all(item.pair in held_pairs for item in selected):
            reasons.append("HELD_ASSETS_REMAIN_WITHIN_EXIT_HYSTERESIS")
            reasons.append("TARGET_WEIGHT_CHANGES_BELOW_REBALANCE_THRESHOLD")
        entry_threshold = self.policy.effective_entry_score_hurdle
        waiting = any(
            item.eligible
            and item.pair not in held_pairs
            and item.score is not None
            and item.score > entry_threshold
            and (
                confirmation_counts.get(item.pair, 0)
                if confirmation_counts is not None
                else int(previous_scores.get(item.pair, float("-inf")) > entry_threshold)
            )
            < self.policy.required_entry_confirmations - 1
            for item in assessments
        )
        if waiting:
            reasons.append("NEW_ENTRY_WAITING_FOR_SECOND_CONFIRMATION")
        if not selected:
            reasons.append("NO_ASSET_PASSED_ENTRY_OR_HOLD_RULES")
        return tuple(dict.fromkeys(reasons or ["NO_ACTION_REQUIRED"]))
