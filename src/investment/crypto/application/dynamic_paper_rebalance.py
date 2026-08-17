"""Dynamic-Universe selection and safe Paper rebalance planning/execution."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from investment.core.domain.observation import require_utc
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
    maximum_daily_fee_fraction: Decimal = Decimal("0.005")
    maximum_daily_realized_loss_fraction: Decimal = Decimal("0.02")

    @property
    def round_trip_cost_hurdle(self) -> float:
        return float(Decimal("2") * (self.exchange_fee_rate + self.estimated_slippage_rate))


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


@dataclass(frozen=True, slots=True)
class SelectedAsset:
    pair: str
    score: float
    target_weight: Decimal
    reason: str


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


class DynamicPaperRebalanceService:
    def __init__(
        self,
        history: UniverseHistory,
        market_data: CryptoMarketDataProvider,
        repository: PaperPortfolioRepository,
        gateway_factory: PaperExchangeGatewayFactory,
        policy: DynamicUniversePolicy | None = None,
    ) -> None:
        self._history = history
        self._market_data = market_data
        self._repository = repository
        self._gateway_factory = gateway_factory
        self.policy = policy or DynamicUniversePolicy()

    def run(self, command: DynamicPaperRebalanceCommand) -> DynamicPaperRebalanceResult:
        as_of = require_utc(command.as_of, "as_of")
        snapshot = self._history.known_at(as_of)
        if snapshot is None:
            raise ValueError("no universe snapshot was known at the rebalance time")
        portfolio = self._repository.get(command.portfolio_id)
        held_pairs = {
            f"{position.asset.symbol}{portfolio.cash_asset.symbol}"
            for position in portfolio.positions
        }
        candidates, candles = self._assess(snapshot, as_of, held_pairs)
        prices = {symbol: values[-1].close for symbol, values in candles.items() if values}
        previous_scores = self._previous_scores(command.portfolio_id)
        recent_exits = self._recent_exits(command.portfolio_id, as_of)
        selected, selection_reasons = self._select(
            candidates, held_pairs, previous_scores, recent_exits
        )
        target_weights = {item.pair: item.target_weight for item in selected}
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
        risk_violations = self._daily_risk_violations(portfolio.portfolio_id, equity, as_of)
        plans, intents = self._plan(
            snapshot,
            portfolio,
            equity,
            prices,
            target_weights,
            as_of,
            block_buys=bool(risk_violations),
        )
        decision_reasons = self._decision_reasons(
            candidates,
            selected,
            plans,
            held_pairs,
            previous_scores,
            risk_violations,
            selection_reasons,
        )
        if command.execute:
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
        )
        self._save_decision(result, command.execute)
        return result

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
            score = float(
                0.45 * momentum_1h + 0.35 * momentum_4h + 0.20 * momentum_24h - 0.10 * volatility
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
                )
            )
        return tuple(assessments), candles_by_symbol

    def _select(
        self,
        assessments: tuple[CandidateAssessment, ...],
        held_pairs: set[str],
        previous_scores: dict[str, float],
        recent_exits: set[str],
    ) -> tuple[tuple[SelectedAsset, ...], tuple[str, ...]]:
        liquid = sorted(
            (item for item in assessments if item.eligible),
            key=lambda item: (-(item.average_quote_volume or Decimal("0")), item.pair),
        )[: self.policy.maximum_candidates]
        scored = sorted(liquid, key=lambda item: (-float(item.score or 0), item.pair))
        ranks = {item.pair: rank for rank, item in enumerate(scored, start=1)}
        retained = [
            item
            for item in scored
            if item.pair in held_pairs
            and item.score is not None
            and item.score > self.policy.exit_score_hurdle
            and ranks[item.pair] <= self.policy.maximum_hold_rank
        ]
        entrants = [
            item
            for item in scored
            if item.pair not in held_pairs
            and item.pair not in recent_exits
            and item.score is not None
            and item.score > max(self.policy.entry_score_hurdle, self.policy.round_trip_cost_hurdle)
            and (
                self.policy.required_entry_confirmations <= 1
                or previous_scores.get(item.pair, float("-inf"))
                > max(self.policy.entry_score_hurdle, self.policy.round_trip_cost_hurdle)
            )
        ]
        cooldown_blocked = any(
            item.pair in recent_exits
            and item.score is not None
            and item.score > max(self.policy.entry_score_hurdle, self.policy.round_trip_cost_hurdle)
            for item in scored
        )
        ranked = sorted(
            retained,
            key=lambda item: (-float(item.score or 0), item.pair),
        )[: self.policy.maximum_positions]
        replacement_blocked = False
        for entrant in entrants:
            if len(ranked) < self.policy.maximum_positions:
                ranked.append(entrant)
                continue
            weakest = min(ranked, key=lambda item: (float(item.score or 0), item.pair))
            if float(entrant.score or 0) >= (
                float(weakest.score or 0) + self.policy.minimum_replacement_score_advantage
            ):
                ranked.remove(weakest)
                ranked.append(entrant)
            else:
                replacement_blocked = True
        ranked.sort(key=lambda item: (-float(item.score or 0), item.pair))
        if not ranked:
            reasons = []
            if cooldown_blocked:
                reasons.append("NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN")
            if replacement_blocked:
                reasons.append("REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT")
            return (), tuple(reasons)
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
                    "HELD_WITHIN_EXIT_HYSTERESIS"
                    if item.pair in held_pairs
                    else "ENTRY_CONFIRMED_ABOVE_COST_HURDLE"
                ),
            )
            for item in ranked
        )
        reasons = []
        if cooldown_blocked:
            reasons.append("NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN")
        if replacement_blocked:
            reasons.append("REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT")
        return selected, tuple(reasons)

    def _recent_exits(self, portfolio_id: str, as_of: datetime) -> set[str]:
        cutoff = as_of - self.policy.reentry_cooldown
        return {
            item.pair
            for item in self._repository.list_executions(portfolio_id, 1000)
            if item.side is OrderSide.SELL and cutoff < item.executed_at <= as_of
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
            if side is OrderSide.BUY and block_buys:
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
        records = self._repository.list_rebalance_decisions(portfolio_id, 1)
        if not records:
            return {}
        payload = json.loads(records[0].assessments_json)
        return {
            str(item["pair"]): float(item["score"])
            for item in payload
            if isinstance(item, dict) and item.get("score") is not None
        }

    def _daily_risk_violations(
        self, portfolio_id: str, equity: Decimal, as_of: datetime
    ) -> tuple[str, ...]:
        day_start = as_of.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        executions = tuple(
            item
            for item in self._repository.list_executions(portfolio_id, 1000)
            if item.executed_at >= day_start
        )
        turnover = sum((item.quantity * item.price for item in executions), Decimal("0"))
        fees = sum((item.fee for item in executions), Decimal("0"))
        realized = sum((item.realized_pnl for item in executions), Decimal("0"))
        violations = []
        if turnover >= equity * self.policy.maximum_daily_turnover_fraction:
            violations.append("DAILY_TURNOVER_BUDGET_EXHAUSTED")
        if fees >= equity * self.policy.maximum_daily_fee_fraction:
            violations.append("DAILY_FEE_BUDGET_EXHAUSTED")
        if realized <= -(equity * self.policy.maximum_daily_realized_loss_fraction):
            violations.append("DAILY_REALIZED_LOSS_LIMIT")
        return tuple(violations)

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
    ) -> tuple[str, ...]:
        if plans:
            return ("ORDERS_CREATED_FOR_TARGET_WEIGHT_CHANGES",)
        reasons = list(selection_reasons)
        if risk_violations:
            reasons.append("NEW_BUYS_BLOCKED_BY_DAILY_RISK_BUDGET")
        if selected and all(item.pair in held_pairs for item in selected):
            reasons.append("HELD_ASSETS_REMAIN_WITHIN_EXIT_HYSTERESIS")
            reasons.append("TARGET_WEIGHT_CHANGES_BELOW_REBALANCE_THRESHOLD")
        entry_threshold = max(
            self.policy.entry_score_hurdle, self.policy.round_trip_cost_hurdle
        )
        waiting = any(
            item.eligible
            and item.pair not in held_pairs
            and item.score is not None
            and item.score > entry_threshold
            and previous_scores.get(item.pair, float("-inf")) <= entry_threshold
            for item in assessments
        )
        if waiting:
            reasons.append("NEW_ENTRY_WAITING_FOR_SECOND_CONFIRMATION")
        if not selected:
            reasons.append("NO_ASSET_PASSED_ENTRY_OR_HOLD_RULES")
        return tuple(dict.fromkeys(reasons or ["NO_ACTION_REQUIRED"]))
