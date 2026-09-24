"""SQLite repository for immutable intraday derivative snapshots and signals."""

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from investment.core.domain.observation import require_utc
from investment.crypto.derivatives.domain import (
    BasisSource,
    CoinbasePriceObservation,
    CrowdingSide,
    CrowdingSignal,
    CrowdingState,
    DerivativesSnapshot,
    LiquidatedPosition,
    LiquidationEvent,
    MarketStreamStatus,
    SqueezeSignal,
    SqueezeState,
    StreamState,
)
from investment.crypto.derivatives.mark_price import MarkPriceObservation


class SqliteDerivativesObservationRepository:
    def __init__(self, path: str | Path = "data/observations/crypto-derivatives.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
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
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mark_price_observation (
                    symbol TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    mark_price TEXT NOT NULL,
                    index_price TEXT NOT NULL,
                    funding_rate TEXT NOT NULL,
                    next_funding_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, event_at)
                );
                CREATE INDEX IF NOT EXISTS idx_derivatives_snapshot_symbol_available
                    ON derivatives_snapshot(symbol, available_at);
                CREATE TABLE IF NOT EXISTS squeeze_signal (
                    snapshot_id TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    state TEXT NOT NULL,
                    fuel_score REAL,
                    ignition_score REAL,
                    open_interest_change_15m REAL,
                    open_interest_change_1h REAL,
                    futures_price_change_15m REAL,
                    futures_price_change_1h REAL,
                    spot_return_15m REAL,
                    spot_volume_ratio REAL,
                    spot_breakout INTEGER,
                    short_liquidation_usd_15m REAL,
                    liquidation_confirmed INTEGER NOT NULL,
                    basis_input_rate TEXT,
                    basis_input_source TEXT,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, feature_version),
                    FOREIGN KEY(snapshot_id) REFERENCES derivatives_snapshot(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_squeeze_signal_symbol_as_of
                    ON squeeze_signal(symbol, as_of);
                CREATE TABLE IF NOT EXISTS crowding_signal (
                    snapshot_id TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    state TEXT NOT NULL,
                    dominant_side TEXT NOT NULL,
                    long_crowding_score REAL,
                    short_crowding_score REAL,
                    crowding_intensity REAL,
                    bullish_unwind_score REAL,
                    bearish_unwind_score REAL,
                    confidence REAL NOT NULL,
                    long_liquidation_usd_15m REAL,
                    short_liquidation_usd_15m REAL,
                    liquidation_confirmed INTEGER NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, feature_version),
                    FOREIGN KEY(snapshot_id) REFERENCES derivatives_snapshot(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_crowding_signal_symbol_as_of
                    ON crowding_signal(symbol, as_of);
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
                CREATE INDEX IF NOT EXISTS idx_liquidation_symbol_event_time
                    ON liquidation_event(symbol, event_time);
                CREATE TABLE IF NOT EXISTS coinbase_price_observation (
                    observation_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    price_usd TEXT NOT NULL,
                    source_sequence INTEGER NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_coinbase_product_observed
                    ON coinbase_price_observation(product_id, observed_at);
                CREATE TABLE IF NOT EXISTS market_stream_status (
                    stream_name TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    connected_since TEXT,
                    last_message_at TEXT,
                    last_error TEXT
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(derivatives_snapshot)")
            }
            if "missing_fields_json" not in columns:
                connection.execute(
                    "ALTER TABLE derivatives_snapshot ADD COLUMN "
                    "missing_fields_json TEXT NOT NULL DEFAULT '[]'"
                )
            for column, definition in (
                ("coinbase_price_usd", "TEXT"),
                ("coinbase_premium_rate", "TEXT"),
                ("coinbase_observed_at", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE derivatives_snapshot ADD COLUMN {column} {definition}"
                    )
            signal_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(squeeze_signal)")
            }
            for column, definition in (
                ("basis_input_rate", "TEXT"),
                ("basis_input_source", "TEXT"),
            ):
                if column not in signal_columns:
                    connection.execute(
                        f"ALTER TABLE squeeze_signal ADD COLUMN {column} {definition}"
                    )

    def save_mark_price(self, value: MarkPriceObservation) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO mark_price_observation VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    value.symbol,
                    value.event_at.isoformat(),
                    value.received_at.isoformat(),
                    str(value.mark_price),
                    str(value.index_price),
                    str(value.funding_rate),
                    value.next_funding_at.isoformat(),
                ),
            )

    def mark_price_known_at(self, symbol: str, as_of: datetime) -> MarkPriceObservation | None:
        cutoff = require_utc(as_of, "as_of").isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mark_price_observation WHERE symbol=? AND event_at<=? "
                "AND received_at<=? ORDER BY event_at DESC LIMIT 1",
                (symbol, cutoff, cutoff),
            ).fetchone()
        if row is None:
            return None
        return MarkPriceObservation(
            symbol=str(row["symbol"]),
            event_at=datetime.fromisoformat(row["event_at"]),
            received_at=datetime.fromisoformat(row["received_at"]),
            mark_price=Decimal(row["mark_price"]),
            index_price=Decimal(row["index_price"]),
            funding_rate=Decimal(row["funding_rate"]),
            next_funding_at=datetime.fromisoformat(row["next_funding_at"]),
        )

    def save_snapshot(self, value: DerivativesSnapshot) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO derivatives_snapshot
                   (snapshot_id, symbol, observed_at, available_at, mark_price, index_price,
                    open_interest_usd, funding_rate, basis_rate, global_long_short_ratio,
                    top_position_long_short_ratio, taker_buy_sell_ratio, source,
                    missing_fields_json, coinbase_price_usd, coinbase_premium_rate,
                    coinbase_observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.symbol,
                    value.observed_at.isoformat(),
                    value.available_at.isoformat(),
                    str(value.mark_price),
                    str(value.index_price),
                    str(value.open_interest_usd),
                    str(value.funding_rate),
                    "MISSING_DATA" if value.basis_rate is None else str(value.basis_rate),
                    str(value.global_long_short_ratio),
                    str(value.top_position_long_short_ratio),
                    str(value.taker_buy_sell_ratio),
                    value.source,
                    json.dumps(sorted(value.missing_fields), separators=(",", ":")),
                    _optional_decimal_text(value.coinbase_price_usd),
                    _optional_decimal_text(value.coinbase_premium_rate),
                    (
                        None
                        if value.coinbase_observed_at is None
                        else value.coinbase_observed_at.isoformat()
                    ),
                ),
            )

    def save_liquidation(self, value: LiquidationEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO liquidation_event
                   (event_id, symbol, event_time, trade_time, position, price,
                    quantity, notional_usd, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.event_id,
                    value.symbol,
                    value.event_time.isoformat(),
                    value.trade_time.isoformat(),
                    value.position.value,
                    str(value.price),
                    str(value.quantity),
                    str(value.notional_usd),
                    value.source,
                ),
            )

    def save_coinbase_price(self, value: CoinbasePriceObservation) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO coinbase_price_observation
                   (observation_id, product_id, observed_at, price_usd,
                    source_sequence, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    value.observation_id,
                    value.product_id,
                    value.observed_at.isoformat(),
                    str(value.price_usd),
                    value.source_sequence,
                    value.source,
                ),
            )

    def mark_stream_connected(self, stream_name: str, at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO market_stream_status
                   (stream_name, state, updated_at, connected_since,
                    last_message_at, last_error)
                   VALUES (?, ?, ?, ?, NULL, NULL)
                   ON CONFLICT(stream_name) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    connected_since=excluded.connected_since""",
                (stream_name, StreamState.CONNECTED.value, at.isoformat(), at.isoformat()),
            )

    def mark_stream_message(self, stream_name: str, at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE market_stream_status
                   SET state=?, updated_at=?, last_message_at=?, last_error=NULL
                   WHERE stream_name=?""",
                (StreamState.CONNECTED.value, at.isoformat(), at.isoformat(), stream_name),
            )

    def mark_stream_disconnected(self, stream_name: str, at: datetime, error: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO market_stream_status
                   (stream_name, state, updated_at, connected_since,
                    last_message_at, last_error)
                   VALUES (?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(stream_name) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    connected_since=NULL,
                    last_error=excluded.last_error""",
                (stream_name, StreamState.DISCONNECTED.value, at.isoformat(), error),
            )

    def mark_stream_stale(self, stream_name: str, at: datetime, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO market_stream_status
                   (stream_name, state, updated_at, connected_since,
                    last_message_at, last_error)
                   VALUES (?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(stream_name) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    connected_since=NULL,
                    last_error=excluded.last_error""",
                (stream_name, StreamState.STALE.value, at.isoformat(), error),
            )

    def stream_statuses(self) -> tuple[MarketStreamStatus, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_stream_status ORDER BY stream_name"
            ).fetchall()
        return tuple(self._stream_status(row) for row in rows)

    def continuously_connected_since(self, stream_name: str, at: datetime) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, connected_since FROM market_stream_status WHERE stream_name=?",
                (stream_name,),
            ).fetchone()
        return bool(
            row is not None
            and row["state"] == StreamState.CONNECTED.value
            and row["connected_since"] is not None
            and datetime.fromisoformat(str(row["connected_since"])) <= at
        )

    def liquidation_notional(
        self,
        symbol: str,
        position: LiquidatedPosition,
        start: datetime,
        end: datetime,
    ) -> Decimal:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT notional_usd FROM liquidation_event
                   WHERE symbol=? AND position=? AND event_time>? AND event_time<=?""",
                (
                    symbol.upper(),
                    position.value,
                    start.isoformat(),
                    end.isoformat(),
                ),
            ).fetchall()
        return sum((Decimal(str(row["notional_usd"])) for row in rows), Decimal("0"))

    def short_liquidation_notional(self, symbol: str, start: datetime, end: datetime) -> Decimal:
        return self.liquidation_notional(symbol, LiquidatedPosition.SHORT, start, end)

    def liquidations(self, symbol: str, limit: int = 100) -> tuple[LiquidationEvent, ...]:
        if limit <= 0:
            raise ValueError("liquidation limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM liquidation_event WHERE symbol=?
                   ORDER BY event_time DESC LIMIT ?""",
                (symbol.upper(), limit),
            ).fetchall()
        return tuple(self._liquidation(row) for row in rows)

    def latest_coinbase_price(self, product_id: str, at: datetime) -> CoinbasePriceObservation:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM coinbase_price_observation
                   WHERE product_id=? AND observed_at<=?
                   ORDER BY observed_at DESC LIMIT 1""",
                (product_id.upper(), at.isoformat()),
            ).fetchone()
        if row is None:
            raise KeyError(product_id)
        return self._coinbase_price(row)

    def save_signal(self, value: SqueezeSignal) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO squeeze_signal
                   (snapshot_id, feature_version, symbol, as_of, state, fuel_score,
                    ignition_score, open_interest_change_15m, open_interest_change_1h,
                    futures_price_change_15m, futures_price_change_1h, spot_return_15m,
                    spot_volume_ratio, spot_breakout, short_liquidation_usd_15m,
                    liquidation_confirmed, basis_input_rate, basis_input_source, evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.feature_version,
                    value.symbol,
                    value.as_of.isoformat(),
                    value.state.value,
                    value.fuel_score,
                    value.ignition_score,
                    value.open_interest_change_15m,
                    value.open_interest_change_1h,
                    value.futures_price_change_15m,
                    value.futures_price_change_1h,
                    value.spot_return_15m,
                    value.spot_volume_ratio,
                    None if value.spot_breakout is None else int(value.spot_breakout),
                    value.short_liquidation_usd_15m,
                    int(value.liquidation_confirmed),
                    _optional_decimal_text(value.basis_input_rate),
                    (None if value.basis_input_source is None else value.basis_input_source.value),
                    json.dumps(value.evidence, separators=(",", ":")),
                ),
            )

    def save_crowding_signal(self, value: CrowdingSignal) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO crowding_signal
                   (snapshot_id, feature_version, symbol, as_of, state, dominant_side,
                    long_crowding_score, short_crowding_score, crowding_intensity,
                    bullish_unwind_score, bearish_unwind_score, confidence,
                    long_liquidation_usd_15m, short_liquidation_usd_15m,
                    liquidation_confirmed, evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.feature_version,
                    value.symbol,
                    value.as_of.isoformat(),
                    value.state.value,
                    value.dominant_side.value,
                    value.long_crowding_score,
                    value.short_crowding_score,
                    value.crowding_intensity,
                    value.bullish_unwind_score,
                    value.bearish_unwind_score,
                    value.confidence,
                    value.long_liquidation_usd_15m,
                    value.short_liquidation_usd_15m,
                    int(value.liquidation_confirmed),
                    json.dumps(value.evidence, separators=(",", ":")),
                ),
            )

    def snapshots(self, symbol: str, limit: int = 1000) -> tuple[DerivativesSnapshot, ...]:
        if limit <= 0:
            raise ValueError("snapshot limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM derivatives_snapshot WHERE symbol=?
                   ORDER BY available_at DESC LIMIT ?""",
                (symbol.upper(), limit),
            ).fetchall()
        return tuple(self._snapshot(row) for row in reversed(rows))

    def signals(
        self, symbol: str, limit: int = 100, feature_version: str | None = None
    ) -> tuple[SqueezeSignal, ...]:
        if limit <= 0:
            raise ValueError("signal limit must be positive")
        with self._connect() as connection:
            if feature_version is None:
                rows = connection.execute(
                    """SELECT * FROM squeeze_signal WHERE symbol=?
                       ORDER BY as_of DESC LIMIT ?""",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM squeeze_signal WHERE symbol=? AND feature_version=?
                       ORDER BY as_of DESC LIMIT ?""",
                    (symbol.upper(), feature_version.strip(), limit),
                ).fetchall()
        return tuple(self._signal(row) for row in rows)

    def crowding_signals(
        self, symbol: str, limit: int = 100, feature_version: str = "btc-crowding-v1"
    ) -> tuple[CrowdingSignal, ...]:
        if limit <= 0:
            raise ValueError("crowding signal limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM crowding_signal WHERE symbol=? AND feature_version=?
                   ORDER BY as_of DESC LIMIT ?""",
                (symbol.upper(), feature_version.strip(), limit),
            ).fetchall()
        return tuple(self._crowding_signal(row) for row in rows)

    def crowding_known_at(
        self,
        symbol: str,
        as_of: datetime,
        feature_version: str,
    ) -> tuple[DerivativesSnapshot, CrowdingSignal] | None:
        cutoff = require_utc(as_of, "as_of")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.* FROM crowding_signal c
                   JOIN derivatives_snapshot d ON d.snapshot_id=c.snapshot_id
                   WHERE c.symbol=? AND c.feature_version=?
                     AND c.as_of<=? AND d.available_at<=?
                   ORDER BY c.as_of DESC, c.snapshot_id DESC LIMIT 1""",
                (symbol.upper(), feature_version.strip(), cutoff.isoformat(), cutoff.isoformat()),
            ).fetchone()
            if row is None:
                return None
            snapshot_row = connection.execute(
                "SELECT * FROM derivatives_snapshot WHERE snapshot_id=?",
                (str(row["snapshot_id"]),),
            ).fetchone()
        if snapshot_row is None:
            return None
        return self._snapshot(snapshot_row), self._crowding_signal(row)

    def latest_snapshot(self, symbol: str) -> DerivativesSnapshot:
        values = self.snapshots(symbol, 1)
        if not values:
            raise KeyError(symbol)
        return values[0]

    def latest_signal(self, symbol: str, feature_version: str) -> SqueezeSignal:
        values = self.signals(symbol, 1, feature_version)
        if not values:
            raise KeyError(symbol)
        return values[0]

    def observation_known_at(
        self,
        symbol: str,
        as_of: datetime,
        feature_version: str,
    ) -> tuple[DerivativesSnapshot, SqueezeSignal] | None:
        """Return only a compatible signal/snapshot pair knowable at ``as_of``.

        Both availability predicates are intentional.  They keep this method
        safe if signal and snapshot timestamps ever diverge, and prevent a
        historical replay from falling through to the repository's current
        latest observation.
        """

        cutoff = require_utc(as_of, "as_of")
        normalized_symbol = symbol.strip().upper()
        normalized_version = feature_version.strip()
        if not normalized_symbol or not normalized_version:
            raise ValueError("symbol and feature version are required")
        with self._connect() as connection:
            signal_row = connection.execute(
                """SELECT s.* FROM squeeze_signal s
                   JOIN derivatives_snapshot d ON d.snapshot_id=s.snapshot_id
                   WHERE s.symbol=? AND s.feature_version=?
                     AND s.as_of<=? AND d.available_at<=?
                   ORDER BY s.as_of DESC, s.snapshot_id DESC LIMIT 1""",
                (
                    normalized_symbol,
                    normalized_version,
                    cutoff.isoformat(),
                    cutoff.isoformat(),
                ),
            ).fetchone()
            if signal_row is None:
                return None
            snapshot_row = connection.execute(
                "SELECT * FROM derivatives_snapshot WHERE snapshot_id=?",
                (str(signal_row["snapshot_id"]),),
            ).fetchone()
        if snapshot_row is None:  # Defensive: the JOIN and foreign key should make this impossible.
            return None
        snapshot = self._snapshot(snapshot_row)
        signal = self._signal(signal_row)
        if snapshot.snapshot_id != signal.snapshot_id or snapshot.symbol != signal.symbol:
            raise ValueError("derivatives signal does not match its source snapshot")
        return snapshot, signal

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> DerivativesSnapshot:
        return DerivativesSnapshot(
            symbol=str(row["symbol"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"])),
            available_at=datetime.fromisoformat(str(row["available_at"])),
            mark_price=Decimal(str(row["mark_price"])),
            index_price=Decimal(str(row["index_price"])),
            open_interest_usd=Decimal(str(row["open_interest_usd"])),
            funding_rate=Decimal(str(row["funding_rate"])),
            basis_rate=(
                None
                if str(row["basis_rate"]) == "MISSING_DATA"
                else Decimal(str(row["basis_rate"]))
            ),
            global_long_short_ratio=Decimal(str(row["global_long_short_ratio"])),
            top_position_long_short_ratio=Decimal(str(row["top_position_long_short_ratio"])),
            taker_buy_sell_ratio=Decimal(str(row["taker_buy_sell_ratio"])),
            coinbase_price_usd=_optional_decimal(row["coinbase_price_usd"]),
            coinbase_premium_rate=_optional_decimal(row["coinbase_premium_rate"]),
            coinbase_observed_at=(
                None
                if row["coinbase_observed_at"] is None
                else datetime.fromisoformat(str(row["coinbase_observed_at"]))
            ),
            source=str(row["source"]),
            missing_fields=frozenset(json.loads(str(row["missing_fields_json"]))),
        )

    @staticmethod
    def _signal(row: sqlite3.Row) -> SqueezeSignal:
        breakout = row["spot_breakout"]
        return SqueezeSignal(
            snapshot_id=str(row["snapshot_id"]),
            symbol=str(row["symbol"]),
            as_of=datetime.fromisoformat(str(row["as_of"])),
            state=SqueezeState(str(row["state"])),
            fuel_score=_optional_float(row["fuel_score"]),
            ignition_score=_optional_float(row["ignition_score"]),
            open_interest_change_15m=_optional_float(row["open_interest_change_15m"]),
            open_interest_change_1h=_optional_float(row["open_interest_change_1h"]),
            futures_price_change_15m=_optional_float(row["futures_price_change_15m"]),
            futures_price_change_1h=_optional_float(row["futures_price_change_1h"]),
            spot_return_15m=_optional_float(row["spot_return_15m"]),
            spot_volume_ratio=_optional_float(row["spot_volume_ratio"]),
            spot_breakout=None if breakout is None else bool(breakout),
            short_liquidation_usd_15m=_optional_float(row["short_liquidation_usd_15m"]),
            liquidation_confirmed=bool(row["liquidation_confirmed"]),
            evidence=tuple(str(item) for item in json.loads(row["evidence_json"])),
            basis_input_rate=_optional_decimal(row["basis_input_rate"]),
            basis_input_source=(
                None
                if row["basis_input_source"] is None
                else BasisSource(str(row["basis_input_source"]))
            ),
            feature_version=str(row["feature_version"]),
        )

    @staticmethod
    def _crowding_signal(row: sqlite3.Row) -> CrowdingSignal:
        return CrowdingSignal(
            snapshot_id=str(row["snapshot_id"]),
            symbol=str(row["symbol"]),
            as_of=datetime.fromisoformat(str(row["as_of"])),
            state=CrowdingState(str(row["state"])),
            dominant_side=CrowdingSide(str(row["dominant_side"])),
            long_crowding_score=_optional_float(row["long_crowding_score"]),
            short_crowding_score=_optional_float(row["short_crowding_score"]),
            crowding_intensity=_optional_float(row["crowding_intensity"]),
            bullish_unwind_score=_optional_float(row["bullish_unwind_score"]),
            bearish_unwind_score=_optional_float(row["bearish_unwind_score"]),
            confidence=float(row["confidence"]),
            long_liquidation_usd_15m=_optional_float(row["long_liquidation_usd_15m"]),
            short_liquidation_usd_15m=_optional_float(row["short_liquidation_usd_15m"]),
            liquidation_confirmed=bool(row["liquidation_confirmed"]),
            evidence=tuple(str(item) for item in json.loads(row["evidence_json"])),
            feature_version=str(row["feature_version"]),
        )

    @staticmethod
    def _liquidation(row: sqlite3.Row) -> LiquidationEvent:
        return LiquidationEvent(
            symbol=str(row["symbol"]),
            event_time=datetime.fromisoformat(str(row["event_time"])),
            trade_time=datetime.fromisoformat(str(row["trade_time"])),
            position=LiquidatedPosition(str(row["position"])),
            price=Decimal(str(row["price"])),
            quantity=Decimal(str(row["quantity"])),
            source=str(row["source"]),
        )

    @staticmethod
    def _coinbase_price(row: sqlite3.Row) -> CoinbasePriceObservation:
        return CoinbasePriceObservation(
            product_id=str(row["product_id"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"])),
            price_usd=Decimal(str(row["price_usd"])),
            source_sequence=int(row["source_sequence"]),
            source=str(row["source"]),
        )

    @staticmethod
    def _stream_status(row: sqlite3.Row) -> MarketStreamStatus:
        return MarketStreamStatus(
            stream_name=str(row["stream_name"]),
            state=StreamState(str(row["state"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            connected_since=(
                None
                if row["connected_since"] is None
                else datetime.fromisoformat(str(row["connected_since"]))
            ),
            last_message_at=(
                None
                if row["last_message_at"] is None
                else datetime.fromisoformat(str(row["last_message_at"]))
            ),
            last_error=None if row["last_error"] is None else str(row["last_error"]),
        )


def _optional_float(value: object) -> float | None:
    return None if value is None else float(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
