"""Transactional, idempotent SQLite paper portfolio accounting."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import ClassVar

from investment.crypto.domain.accounting import (
    PaperExecutionRecord,
    PaperPortfolioSnapshot,
    PaperPosition,
    PaperRebalanceDecisionRecord,
)
from investment.crypto.domain.market import Asset, AssetKind
from investment.crypto.domain.order import ApprovedOrder, OrderSide
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.ports.exchange import ExecutionReport


class SqlitePaperPortfolioRepository:
    """SQLite Paper ledger with process-local, once-only schema initialization.

    FastAPI creates a lightweight repository for read-only portfolio requests. Schema
    migration must happen during process startup, not on every dashboard refresh where
    it can contend with an in-flight Paper execution transaction.
    """

    _initialization_guard: ClassVar[RLock] = RLock()
    _initialized_database_ids: ClassVar[set[tuple[str, int]]] = set()

    def __init__(self, path: str | Path = "data/paper/crypto-trading.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._initialization_guard:
            database_id = self._database_id()
            if database_id not in self._initialized_database_ids:
                self._initialize()
                self._initialized_database_ids.add(self._database_id())

    def _database_id(self) -> tuple[str, int]:
        """Use inode as well as path so a replaced database reinitializes."""
        resolved = self.path.resolve()
        try:
            inode = resolved.stat().st_ino
        except FileNotFoundError:
            inode = -1
        return str(resolved), inode

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_portfolio (
                    portfolio_id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    cash_asset TEXT NOT NULL,
                    cash_balance TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_position (
                    portfolio_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    average_cost TEXT NOT NULL,
                    PRIMARY KEY (portfolio_id, asset),
                    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolio(portfolio_id)
                );
                CREATE TABLE IF NOT EXISTS paper_execution (
                    order_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE,
                    portfolio_id TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    fee TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    executed_at TEXT NOT NULL,
                    execution_model_version TEXT NOT NULL DEFAULT 'paper-fill-v1',
                    fee_rate TEXT NOT NULL DEFAULT '0.0005',
                    slippage_rate TEXT NOT NULL DEFAULT '0',
                    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolio(portfolio_id)
                );
                CREATE TABLE IF NOT EXISTS paper_rebalance_decision (
                    decision_id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    universe_observed_at TEXT NOT NULL,
                    execute INTEGER NOT NULL,
                    equity TEXT NOT NULL,
                    assessments_json TEXT NOT NULL,
                    selected_json TEXT NOT NULL,
                    orders_json TEXT NOT NULL,
                    risk_violations_json TEXT NOT NULL,
                    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
                    market_context_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolio(portfolio_id)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(paper_rebalance_decision)")
            }
            if "decision_reasons_json" not in columns:
                connection.execute(
                    "ALTER TABLE paper_rebalance_decision "
                    "ADD COLUMN decision_reasons_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "market_context_json" not in columns:
                connection.execute(
                    "ALTER TABLE paper_rebalance_decision "
                    "ADD COLUMN market_context_json TEXT NOT NULL DEFAULT '{}'"
                )
            execution_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(paper_execution)")
            }
            for name, declaration in (
                ("execution_model_version", "TEXT NOT NULL DEFAULT 'paper-fill-v1'"),
                ("fee_rate", "TEXT NOT NULL DEFAULT '0.0005'"),
                ("slippage_rate", "TEXT NOT NULL DEFAULT '0'"),
            ):
                if name not in execution_columns:
                    connection.execute(
                        f"ALTER TABLE paper_execution ADD COLUMN {name} {declaration}"
                    )

    def create(self, portfolio: TradingPortfolio) -> PaperPortfolioSnapshot:
        if portfolio.positions:
            raise ValueError("paper portfolio creation requires an empty initial position set")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT purpose, cash_asset, cash_balance "
                "FROM paper_portfolio WHERE portfolio_id = ?",
                (portfolio.portfolio_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO paper_portfolio
                       (portfolio_id, purpose, cash_asset, cash_balance, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        portfolio.portfolio_id,
                        portfolio.purpose.value,
                        portfolio.cash_asset.symbol,
                        str(portfolio.cash_balance),
                        now,
                        now,
                    ),
                )
            elif (
                existing["purpose"] != portfolio.purpose.value
                or existing["cash_asset"] != portfolio.cash_asset.symbol
            ):
                raise ValueError("existing portfolio identity does not match")
        return self.get(portfolio.portfolio_id)

    def apply_execution(self, order: ApprovedOrder, report: ExecutionReport) -> bool:
        intent = order.intent
        if report.side is not intent.side:
            raise ValueError("execution side does not match order intent")
        if report.filled_quantity <= 0 or report.filled_quantity > intent.quantity:
            raise ValueError("invalid filled quantity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM paper_execution WHERE order_id = ? OR intent_id = ?",
                (report.order_id, intent.intent_id),
            ).fetchone()
            if duplicate is not None:
                return False
            portfolio = connection.execute(
                "SELECT * FROM paper_portfolio WHERE portfolio_id = ?",
                (intent.portfolio_id,),
            ).fetchone()
            if portfolio is None:
                raise KeyError(intent.portfolio_id)
            if portfolio["purpose"] != intent.purpose.value:
                raise ValueError("portfolio purpose mismatch")
            if portfolio["cash_asset"] != intent.pair.quote.symbol:
                raise ValueError("order quote asset does not match portfolio cash")
            cash = Decimal(portfolio["cash_balance"])
            position = connection.execute(
                "SELECT quantity, average_cost FROM paper_position "
                "WHERE portfolio_id = ? AND asset = ?",
                (intent.portfolio_id, intent.pair.base.symbol),
            ).fetchone()
            old_quantity = Decimal(position["quantity"]) if position else Decimal("0")
            old_average = Decimal(position["average_cost"]) if position else Decimal("0")
            notional = report.filled_quantity * report.average_price
            realized_pnl = Decimal("0")
            if intent.side is OrderSide.BUY:
                required_cash = notional + report.fee
                if required_cash > cash:
                    raise ValueError("insufficient paper cash")
                new_quantity = old_quantity + report.filled_quantity
                new_average = (old_quantity * old_average + notional + report.fee) / new_quantity
                cash -= required_cash
            else:
                if report.filled_quantity > old_quantity:
                    raise ValueError("insufficient paper position")
                new_quantity = old_quantity - report.filled_quantity
                new_average = old_average if new_quantity else Decimal("0")
                cash += notional - report.fee
                realized_pnl = (
                    report.average_price - old_average
                ) * report.filled_quantity - report.fee
            self._upsert_position(
                connection,
                intent.portfolio_id,
                intent.pair.base.symbol,
                new_quantity,
                new_average,
            )
            connection.execute(
                "UPDATE paper_portfolio SET cash_balance = ?, updated_at = ? "
                "WHERE portfolio_id = ?",
                (str(cash), report.executed_at.isoformat(), intent.portfolio_id),
            )
            connection.execute(
                """INSERT INTO paper_execution
                   (order_id, intent_id, portfolio_id, pair, side, quantity, price, fee,
                    realized_pnl, executed_at, execution_model_version, fee_rate, slippage_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report.order_id,
                    intent.intent_id,
                    intent.portfolio_id,
                    intent.pair.symbol,
                    intent.side.value,
                    str(report.filled_quantity),
                    str(report.average_price),
                    str(report.fee),
                    str(realized_pnl),
                    report.executed_at.isoformat(),
                    report.execution_model_version,
                    str(report.fee_rate),
                    str(report.slippage_rate),
                ),
            )
        return True

    @staticmethod
    def _upsert_position(
        connection: sqlite3.Connection,
        portfolio_id: str,
        asset: str,
        quantity: Decimal,
        average_cost: Decimal,
    ) -> None:
        if abs(quantity) < Decimal("1e-18"):
            connection.execute(
                "DELETE FROM paper_position WHERE portfolio_id = ? AND asset = ?",
                (portfolio_id, asset),
            )
            return
        connection.execute(
            """INSERT INTO paper_position (portfolio_id, asset, quantity, average_cost)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(portfolio_id, asset) DO UPDATE SET
                 quantity = excluded.quantity,
                 average_cost = excluded.average_cost""",
            (portfolio_id, asset, str(quantity), str(average_cost)),
        )

    def get(self, portfolio_id: str) -> PaperPortfolioSnapshot:
        with self._connect() as connection:
            portfolio = connection.execute(
                "SELECT * FROM paper_portfolio WHERE portfolio_id = ?", (portfolio_id,)
            ).fetchone()
            if portfolio is None:
                raise KeyError(portfolio_id)
            rows = connection.execute(
                "SELECT asset, quantity, average_cost FROM paper_position "
                "WHERE portfolio_id = ? ORDER BY asset",
                (portfolio_id,),
            ).fetchall()
        return PaperPortfolioSnapshot(
            portfolio_id=portfolio["portfolio_id"],
            purpose=PortfolioPurpose(portfolio["purpose"]),
            cash_asset=Asset(portfolio["cash_asset"], AssetKind.CASH),
            cash_balance=Decimal(portfolio["cash_balance"]),
            positions=tuple(
                PaperPosition(
                    Asset(row["asset"]),
                    Decimal(row["quantity"]),
                    Decimal(row["average_cost"]),
                )
                for row in rows
            ),
            updated_at=datetime.fromisoformat(portfolio["updated_at"]),
        )

    def list_executions(
        self, portfolio_id: str, limit: int = 100
    ) -> tuple[PaperExecutionRecord, ...]:
        if limit <= 0:
            raise ValueError("execution limit must be positive")
        self.get(portfolio_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_execution WHERE portfolio_id = ? "
                "ORDER BY executed_at DESC LIMIT ?",
                (portfolio_id, limit),
            ).fetchall()
        return tuple(
            PaperExecutionRecord(
                str(row["order_id"]),
                str(row["intent_id"]),
                str(row["portfolio_id"]),
                str(row["pair"]),
                OrderSide(row["side"]),
                Decimal(row["quantity"]),
                Decimal(row["price"]),
                Decimal(row["fee"]),
                Decimal(row["realized_pnl"]),
                datetime.fromisoformat(row["executed_at"]),
                str(row["execution_model_version"]),
                Decimal(row["fee_rate"]),
                Decimal(row["slippage_rate"]),
            )
            for row in rows
        )

    def save_rebalance_decision(self, decision: PaperRebalanceDecisionRecord) -> None:
        import json

        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO paper_rebalance_decision
                   (decision_id, portfolio_id, strategy_version, as_of, universe_observed_at,
                    execute, equity, assessments_json, selected_json, orders_json,
                    risk_violations_json, decision_reasons_json, market_context_json,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.decision_id,
                    decision.portfolio_id,
                    decision.strategy_version,
                    decision.as_of.isoformat(),
                    decision.universe_observed_at.isoformat(),
                    int(decision.execute),
                    str(decision.equity),
                    decision.assessments_json,
                    decision.selected_json,
                    decision.orders_json,
                    json.dumps(decision.risk_violations),
                    json.dumps(decision.decision_reasons),
                    decision.market_context_json,
                    decision.status,
                    decision.created_at.isoformat(),
                ),
            )

    def list_rebalance_decisions(
        self, portfolio_id: str, limit: int = 100
    ) -> tuple[PaperRebalanceDecisionRecord, ...]:
        import json

        if limit <= 0:
            raise ValueError("decision limit must be positive")
        self.get(portfolio_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_rebalance_decision WHERE portfolio_id = ? "
                "ORDER BY as_of DESC LIMIT ?",
                (portfolio_id, limit),
            ).fetchall()
        return tuple(
            PaperRebalanceDecisionRecord(
                str(row["decision_id"]),
                str(row["portfolio_id"]),
                str(row["strategy_version"]),
                datetime.fromisoformat(row["as_of"]),
                datetime.fromisoformat(row["universe_observed_at"]),
                bool(row["execute"]),
                Decimal(row["equity"]),
                str(row["assessments_json"]),
                str(row["selected_json"]),
                str(row["orders_json"]),
                tuple(json.loads(row["risk_violations_json"])),
                tuple(json.loads(row["decision_reasons_json"])),
                str(row["status"]),
                datetime.fromisoformat(row["created_at"]),
                str(row["market_context_json"]),
            )
            for row in rows
        )
