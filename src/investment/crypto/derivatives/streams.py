"""Resilient public WebSocket collectors for liquidation and Coinbase prices."""

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, cast

from investment.crypto.derivatives.domain import (
    CoinbasePriceObservation,
    LiquidatedPosition,
    LiquidationEvent,
)
from investment.crypto.derivatives.mark_price import parse_mark_price
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository

logger = logging.getLogger(__name__)

BINANCE_LIQUIDATION_STREAM = "binance_btcusdt_force_order"
COINBASE_TICKER_STREAM = "coinbase_btc_usd_ticker_batch"
BINANCE_MARK_PRICE_STREAM = "binance_btcusdt_mark_price"


class StreamStaleError(TimeoutError):
    """Raised when an application-level heartbeat has stopped arriving."""


class WebSocketConnection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], AbstractContextManager[WebSocketConnection]]


class StreamOwnershipLock(Protocol):
    """Process-safe exclusive ownership for the shared WebSocket collectors."""

    def acquire(self) -> bool: ...

    def refresh(self) -> None: ...

    def release(self) -> None: ...


class DirectoryStreamOwnershipLock:
    """Atomic shared-volume ownership with a renewable stale-owner heartbeat."""

    def __init__(
        self,
        path: str | Path,
        *,
        stale_after_seconds: float = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stream ownership stale interval must be positive")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stale_after_seconds = stale_after_seconds
        self.clock = clock
        self._token = uuid.uuid4().hex
        self._owned = False
        self._guard = threading.Lock()

    def acquire(self) -> bool:
        with self._guard:
            if self._owned:
                self._refresh_owned()
                return True
            if self._try_create():
                return True
            try:
                age = self.clock() - self.path.stat().st_mtime
            except FileNotFoundError:
                return self._try_create()
            if age < self.stale_after_seconds:
                return False
            stale = self.path.with_name(f".{self.path.name}.stale-{self._token}")
            try:
                os.rename(self.path, stale)
            except FileNotFoundError:
                return False
            try:
                return self._try_create()
            finally:
                self._remove_directory(stale)

    def refresh(self) -> None:
        with self._guard:
            if self._owned:
                self._refresh_owned()

    def release(self) -> None:
        with self._guard:
            if not self._owned:
                return
            self._owned = False
            owner = self.path / "owner"
            try:
                if owner.read_text().splitlines()[0] != self._token:
                    return
            except FileNotFoundError:
                return
            owner.unlink(missing_ok=True)
            with suppress(FileNotFoundError):
                self.path.rmdir()

    def _try_create(self) -> bool:
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            return False
        try:
            descriptor = os.open(self.path / "owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, f"{self._token}\npid={os.getpid()}\n".encode())
            finally:
                os.close(descriptor)
            os.utime(self.path, (self.clock(), self.clock()))
            self._owned = True
            return True
        except Exception:
            self._remove_directory(self.path)
            raise

    def _refresh_owned(self) -> None:
        owner = self.path / "owner"
        if owner.read_text().splitlines()[0] != self._token:
            self._owned = False
            raise RuntimeError("derivatives WebSocket ownership was lost")
        now = self.clock()
        os.utime(self.path, (now, now))

    @staticmethod
    def _remove_directory(path: Path) -> None:
        try:
            (path / "owner").unlink(missing_ok=True)
            path.rmdir()
        except FileNotFoundError:
            pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _connect(url: str) -> AbstractContextManager[WebSocketConnection]:
    from websockets.sync.client import connect

    return cast(
        AbstractContextManager[WebSocketConnection],
        connect(
            url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        ),
    )


class DerivativesMarketStreams:
    """Own configured daemon stream workers with bounded reconnect backoff."""

    def __init__(
        self,
        repository: SqliteDerivativesObservationRepository,
        *,
        binance_url: str = "wss://fstream.binance.com/market/ws/btcusdt@forceOrder",
        coinbase_url: str = "wss://advanced-trade-ws.coinbase.com",
        connection_factory: ConnectionFactory = _connect,
        clock: Callable[[], datetime] = _utc_now,
        reconnect_delays: tuple[float, ...] = (1, 5, 30),
        ownership_lock: StreamOwnershipLock | None = None,
        supervisor_interval_seconds: float = 1,
        mark_price_url: str | None = None,
    ) -> None:
        if not reconnect_delays or any(value < 0 for value in reconnect_delays):
            raise ValueError("stream reconnect delays must be non-negative")
        if supervisor_interval_seconds <= 0:
            raise ValueError("stream supervisor interval must be positive")
        self.repository = repository
        self._clock = clock
        self._stop = threading.Event()
        self._lifecycle_guard = threading.Lock()
        self._supervisor: threading.Thread | None = None
        self._closed = False
        self._supervisor_interval_seconds = supervisor_interval_seconds
        self._ownership_lock = ownership_lock or DirectoryStreamOwnershipLock(
            repository.path.with_suffix(f"{repository.path.suffix}.streams.owner")
        )
        self._workers: tuple[_StreamWorker, ...] = (
            _StreamWorker(
                BINANCE_LIQUIDATION_STREAM,
                binance_url,
                repository,
                self._handle_binance,
                None,
                self._stop,
                connection_factory,
                clock,
                reconnect_delays,
                None,
            ),
            _StreamWorker(
                COINBASE_TICKER_STREAM,
                coinbase_url,
                repository,
                self._handle_coinbase,
                _coinbase_subscriptions,
                self._stop,
                connection_factory,
                clock,
                reconnect_delays,
                15,
            ),
        )
        if mark_price_url is not None:
            self._workers += (
                _StreamWorker(
                    BINANCE_MARK_PRICE_STREAM,
                    mark_price_url,
                    repository,
                    self._handle_mark_price,
                    None,
                    self._stop,
                    connection_factory,
                    clock,
                    reconnect_delays,
                    15,
                ),
            )

    def _handle_mark_price(self, message: str | bytes) -> bool:
        observation = parse_mark_price(message, self._clock())
        if observation is None:
            return False
        self.repository.save_mark_price(observation)
        return True

    def start(self) -> None:
        with self._lifecycle_guard:
            if self._closed:
                return
            if self._supervisor is not None and self._supervisor.is_alive():
                return
            self._stop.clear()
            self._supervisor = threading.Thread(
                target=self._supervise,
                name="market-stream-supervisor",
                daemon=True,
            )
            self._supervisor.start()

    def stop(self) -> None:
        with self._lifecycle_guard:
            self._closed = True
            self._stop.set()
            for worker in self._workers:
                worker.request_stop()
            supervisor = self._supervisor
            if supervisor is not None:
                supervisor.join(timeout=15)
                if not supervisor.is_alive():
                    self._supervisor = None

    @property
    def is_running(self) -> bool:
        with self._lifecycle_guard:
            return self._supervisor is not None and self._supervisor.is_alive()

    def _supervise(self) -> None:
        owns_streams = False
        try:
            while not self._stop.is_set():
                if not owns_streams:
                    owns_streams = self._ownership_lock.acquire()
                    if not owns_streams:
                        self._stop.wait(self._supervisor_interval_seconds)
                        continue
                    logger.info("acquired derivatives WebSocket collector ownership")
                if self._stop.is_set():
                    break
                self._ownership_lock.refresh()
                for worker in self._workers:
                    worker.start()
                self._stop.wait(self._supervisor_interval_seconds)
        finally:
            self._stop.set()
            if owns_streams:
                for worker in self._workers:
                    worker.request_stop()
                while any(worker.is_alive for worker in self._workers):
                    for worker in self._workers:
                        worker.request_stop()
                        worker.join(timeout=1)
                self._ownership_lock.release()
                logger.info("released derivatives WebSocket collector ownership")

    def _handle_binance(self, message: str | bytes) -> bool:
        event = parse_binance_liquidation(message)
        if event is None:
            return False
        self.repository.save_liquidation(event)
        return True

    def _handle_coinbase(self, message: str | bytes) -> bool:
        observations = parse_coinbase_prices(message)
        for observation in observations:
            self.repository.save_coinbase_price(observation)
        return bool(observations) or is_coinbase_heartbeat(message)


class _StreamWorker:
    def __init__(
        self,
        name: str,
        url: str,
        repository: SqliteDerivativesObservationRepository,
        handler: Callable[[str | bytes], bool],
        subscriptions: Callable[[WebSocketConnection], None] | None,
        stop: threading.Event,
        connection_factory: ConnectionFactory,
        clock: Callable[[], datetime],
        reconnect_delays: tuple[float, ...],
        message_timeout_seconds: float | None,
    ) -> None:
        self.name = name
        self.url = url
        self.repository = repository
        self.handler = handler
        self.subscriptions = subscriptions
        self.stop_event = stop
        self.connection_factory = connection_factory
        self.clock = clock
        self.reconnect_delays = reconnect_delays
        self.message_timeout_seconds = message_timeout_seconds
        self._thread: threading.Thread | None = None
        self._connection_guard = threading.Lock()
        self._active_connection: WebSocketConnection | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"market-stream-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> None:
        with self._connection_guard:
            connection = self._active_connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.warning("failed to close %s during shutdown", self.name)

    def _run(self) -> None:
        failures = 0
        while not self.stop_event.is_set():
            stale_triggered = threading.Event()
            try:
                with self.connection_factory(self.url) as connection:
                    with self._connection_guard:
                        self._active_connection = connection
                    try:
                        connected_at = self.clock()
                        self.repository.mark_stream_connected(self.name, connected_at)
                        if self.subscriptions is not None:
                            self.subscriptions(connection)
                        failures = 0
                        last_received_at = [connected_at]
                        watchdog_stop = threading.Event()
                        watchdog = self._start_watchdog(
                            connection,
                            last_received_at,
                            watchdog_stop,
                            stale_triggered,
                        )
                        try:
                            while not self.stop_event.is_set():
                                try:
                                    message = connection.recv(timeout=1)
                                except TimeoutError:
                                    now = self.clock()
                                    timeout = self.message_timeout_seconds
                                    if timeout is not None and message_is_stale(
                                        last_received_at[0], now, timeout
                                    ):
                                        stale_triggered.set()
                                        raise StreamStaleError(
                                            f"no application message for {timeout:g}s"
                                        ) from None
                                    continue
                                try:
                                    handled = self.handler(message)
                                except (KeyError, TypeError, ValueError, InvalidOperation):
                                    logger.warning("ignored malformed %s message", self.name)
                                    self._raise_if_stale(last_received_at[0])
                                    continue
                                if handled:
                                    handled_at = self.clock()
                                    last_received_at[0] = handled_at
                                    self.repository.mark_stream_message(self.name, handled_at)
                                else:
                                    self._raise_if_stale(last_received_at[0])
                        finally:
                            watchdog_stop.set()
                            if watchdog is not None:
                                watchdog.join(timeout=2)
                    finally:
                        with self._connection_guard:
                            if self._active_connection is connection:
                                self._active_connection = None
            except StreamStaleError as error:
                if self.stop_event.is_set():
                    break
                self.repository.mark_stream_stale(self.name, self.clock(), str(error))
                delay = self.reconnect_delays[min(failures, len(self.reconnect_delays) - 1)]
                failures += 1
                self.stop_event.wait(delay)
            except Exception as error:
                if self.stop_event.is_set():
                    break
                if not stale_triggered.is_set():
                    self.repository.mark_stream_disconnected(
                        self.name, self.clock(), f"{type(error).__name__}: {error}"[:500]
                    )
                delay = self.reconnect_delays[min(failures, len(self.reconnect_delays) - 1)]
                failures += 1
                self.stop_event.wait(delay)
        self.repository.mark_stream_disconnected(self.name, self.clock(), None)

    def _raise_if_stale(self, last_received_at: datetime) -> None:
        timeout = self.message_timeout_seconds
        if timeout is not None and message_is_stale(last_received_at, self.clock(), timeout):
            raise StreamStaleError(f"no application message for {timeout:g}s")

    def _start_watchdog(
        self,
        connection: WebSocketConnection,
        last_received_at: list[datetime],
        watchdog_stop: threading.Event,
        stale_triggered: threading.Event,
    ) -> threading.Thread | None:
        timeout = self.message_timeout_seconds
        if timeout is None:
            return None

        def monitor() -> None:
            while not self.stop_event.is_set() and not watchdog_stop.wait(0.5):
                now = self.clock()
                if not message_is_stale(last_received_at[0], now, timeout):
                    continue
                stale_triggered.set()
                error = f"no application message for {timeout:g}s"
                try:
                    self.repository.mark_stream_stale(self.name, now, error)
                finally:
                    connection.close()
                return

        watchdog = threading.Thread(
            target=monitor,
            name=f"market-stream-watchdog-{self.name}",
            daemon=True,
        )
        watchdog.start()
        return watchdog


def parse_binance_liquidation(message: str | bytes) -> LiquidationEvent | None:
    payload = _json_object(message)
    if payload.get("e") != "forceOrder" or not isinstance(payload.get("o"), dict):
        return None
    order = cast(dict[str, Any], payload["o"])
    side = str(order["S"]).upper()
    position = LiquidatedPosition.SHORT if side == "BUY" else LiquidatedPosition.LONG
    average_price = _positive_decimal(order.get("ap"))
    price = average_price or _required_positive_decimal(order["p"])
    last_quantity = _positive_decimal(order.get("l"))
    quantity = last_quantity or _required_positive_decimal(order["z"])
    return LiquidationEvent(
        symbol=str(order["s"]),
        event_time=_timestamp_ms(payload["E"]),
        trade_time=_timestamp_ms(order["T"]),
        position=position,
        price=price,
        quantity=quantity,
    )


def parse_coinbase_prices(message: str | bytes) -> tuple[CoinbasePriceObservation, ...]:
    payload = _json_object(message)
    if payload.get("channel") not in {"ticker", "ticker_batch"}:
        return ()
    observed_at = _iso_timestamp(payload["timestamp"])
    sequence = int(payload.get("sequence_num", 0))
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("Coinbase ticker events must be a list")
    results: list[CoinbasePriceObservation] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("tickers"), list):
            continue
        for ticker in event["tickers"]:
            if not isinstance(ticker, dict) or ticker.get("product_id") != "BTC-USD":
                continue
            results.append(
                CoinbasePriceObservation(
                    product_id="BTC-USD",
                    observed_at=observed_at,
                    price_usd=_required_positive_decimal(ticker["price"]),
                    source_sequence=sequence,
                )
            )
    return tuple(results)


def is_coinbase_heartbeat(message: str | bytes) -> bool:
    return _json_object(message).get("channel") == "heartbeats"


def message_is_stale(
    last_received_at: datetime,
    now: datetime,
    timeout_seconds: float | None,
) -> bool:
    if timeout_seconds is None:
        return False
    return now - last_received_at >= timedelta(seconds=timeout_seconds)


def _coinbase_subscriptions(connection: WebSocketConnection) -> None:
    connection.send(
        json.dumps(
            {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker_batch"},
            separators=(",", ":"),
        )
    )
    connection.send(
        json.dumps({"type": "subscribe", "channel": "heartbeats"}, separators=(",", ":"))
    )


def _json_object(message: str | bytes) -> dict[str, Any]:
    payload = json.loads(message)
    if not isinstance(payload, dict):
        raise ValueError("WebSocket message must be an object")
    return cast(dict[str, Any], payload)


def _timestamp_ms(value: object) -> datetime:
    return datetime.fromtimestamp(int(str(value)) / 1000, UTC)


def _iso_timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _positive_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _required_positive_decimal(value: object) -> Decimal:
    parsed = _positive_decimal(value)
    if parsed is None:
        raise ValueError("WebSocket numeric field must be positive")
    return parsed
