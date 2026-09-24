"""Public Binance USD-M futures adapter for point-in-time BTC observations."""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any, cast

import httpx

from investment.core.domain.observation import require_utc
from investment.crypto.derivatives.domain import DerivativesSnapshot
from investment.crypto.derivatives.mark_price import MarkPriceObservation

logger = logging.getLogger(__name__)

_RATE_LIMIT_STATUSES = frozenset({418, 429})
_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 120.0
_MAXIMUM_RATE_LIMIT_COOLDOWN_SECONDS = 3 * 24 * 60 * 60.0
_BAN_UNTIL_PATTERN = re.compile(r"\bbanned\s+until\s+(\d{10,16})\b", re.IGNORECASE)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BinanceIntradayDerivativesClient:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        base_url: str = "https://fapi.binance.com",
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 0.2,
        sleeper: Callable[[float], None] | None = None,
        rate_limit_fallback_seconds: float = _DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
        maximum_rate_limit_cooldown_seconds: float = _MAXIMUM_RATE_LIMIT_COOLDOWN_SECONDS,
        mark_price_provider: Callable[[str, datetime], MarkPriceObservation | None] | None = None,
        mark_price_maximum_age_seconds: float = 15,
        official_basis_enabled: bool = True,
    ) -> None:
        self.symbol = symbol.strip().upper()
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._clock = clock
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if rate_limit_fallback_seconds <= 0:
            raise ValueError("rate_limit_fallback_seconds must be positive")
        if maximum_rate_limit_cooldown_seconds < rate_limit_fallback_seconds:
            raise ValueError("maximum_rate_limit_cooldown_seconds must cover the fallback cooldown")
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._sleeper = sleeper or time.sleep
        self._rate_limit_fallback_seconds = rate_limit_fallback_seconds
        self._maximum_rate_limit_cooldown_seconds = maximum_rate_limit_cooldown_seconds
        self._basis_blocked_until: datetime | None = None
        if mark_price_maximum_age_seconds <= 0:
            raise ValueError("mark price maximum age must be positive")
        self._mark_price_provider = mark_price_provider
        self._mark_price_maximum_age = timedelta(seconds=mark_price_maximum_age_seconds)
        self._official_basis_enabled = official_basis_enabled

    def fetch_snapshot(self, as_of: datetime | None = None) -> DerivativesSnapshot:
        available_at = require_utc(as_of or self._clock(), "as_of")
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=20)
        try:
            premium, premium_source = self._premium(client, available_at)
            interest = self._object(client, "/fapi/v1/openInterest", {"symbol": self.symbol})
            global_ratio = self._latest(
                client,
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": self.symbol, "period": "5m", "limit": 2},
            )
            top_position_ratio = self._latest(
                client,
                "/futures/data/topLongShortPositionRatio",
                {"symbol": self.symbol, "period": "5m", "limit": 2},
            )
            taker_ratio = self._latest(
                client,
                "/futures/data/takerlongshortRatio",
                {"symbol": self.symbol, "period": "5m", "limit": 2},
            )
            # Basis is an optional research feature. Fetch it last so a basis-only
            # throttle cannot discard the otherwise complete point-in-time snapshot.
            basis = (
                self._optional_latest(
                    client,
                    "/futures/data/basis",
                    {
                        "pair": self.symbol,
                        "contractType": "PERPETUAL",
                        "period": "5m",
                        "limit": 2,
                    },
                )
                if self._official_basis_enabled
                else None
            )
        finally:
            if owns_client:
                client.close()

        if as_of is None:
            available_at = max(available_at, require_utc(self._clock(), "clock"))

        mark_price = _decimal(premium, "markPrice")
        source_ms = max(
            _integer(premium, "time"),
            _integer(interest, "time"),
            *(() if basis is None else (_integer(basis, "timestamp"),)),
            _integer(global_ratio, "timestamp"),
            _integer(top_position_ratio, "timestamp"),
            _integer(taker_ratio, "timestamp"),
        )
        source_time = datetime.fromtimestamp(source_ms / 1000, UTC)
        observed_at = min(source_time, available_at)
        return DerivativesSnapshot(
            symbol=self.symbol,
            observed_at=observed_at,
            available_at=available_at,
            mark_price=mark_price,
            index_price=_decimal(premium, "indexPrice"),
            open_interest_usd=_decimal(interest, "openInterest") * mark_price,
            funding_rate=_decimal(premium, "lastFundingRate"),
            basis_rate=None if basis is None else _decimal(basis, "basisRate"),
            global_long_short_ratio=_decimal(global_ratio, "longShortRatio"),
            top_position_long_short_ratio=_decimal(top_position_ratio, "longShortRatio"),
            taker_buy_sell_ratio=_decimal(taker_ratio, "buySellRatio"),
            missing_fields=frozenset({"basis_rate"}) if basis is None else frozenset(),
            source=premium_source,
        )

    def _premium(self, client: httpx.Client, as_of: datetime) -> tuple[dict[str, Any], str]:
        observation = None
        if self._mark_price_provider is not None:
            try:
                observation = self._mark_price_provider(self.symbol, as_of)
            except Exception:
                logger.exception("mark price lookup failed; using premiumIndex REST")
        if (
            observation is not None
            and observation.symbol == self.symbol
            and observation.received_at <= as_of
            and timedelta(0) <= as_of - observation.event_at <= self._mark_price_maximum_age
        ):
            return {
                "markPrice": str(observation.mark_price),
                "indexPrice": str(observation.index_price),
                "lastFundingRate": str(observation.funding_rate),
                "time": int(observation.event_at.timestamp() * 1000),
            }, "binance-mark-price-websocket"
        return self._object(
            client, "/fapi/v1/premiumIndex", {"symbol": self.symbol}
        ), "binance_usdm_futures"

    def _object(
        self, client: httpx.Client, path: str, params: dict[str, str | int]
    ) -> dict[str, Any]:
        response = client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected Binance derivatives object for {path}")
        return cast(dict[str, Any], payload)

    def _latest(
        self, client: httpx.Client, path: str, params: dict[str, str | int]
    ) -> dict[str, Any]:
        response = client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[-1], dict):
            raise ValueError(f"unexpected Binance derivatives series for {path}")
        return cast(dict[str, Any], payload[-1])

    def _optional_latest(
        self, client: httpx.Client, path: str, params: dict[str, str | int]
    ) -> dict[str, Any] | None:
        """Preserve an explicit gap when the optional basis series is unavailable."""
        now = require_utc(self._clock(), "clock")
        if self._basis_blocked_until is not None and now < self._basis_blocked_until:
            return None

        for attempt in range(self._retry_attempts):
            response = client.get(f"{self.base_url}{path}", params=params)
            if response.status_code in _RATE_LIMIT_STATUSES:
                self._defer_basis_after_rate_limit(response, now)
                return None
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                self._record_malformed_basis(response, path, "unparseable")
                return None
            if isinstance(payload, dict) and str(payload.get("code")) == "-1003":
                self._defer_basis_after_rate_limit(response, now)
                return None
            if isinstance(payload, list) and payload and isinstance(payload[-1], dict):
                self._basis_blocked_until = None
                return cast(dict[str, Any], payload[-1])
            if not isinstance(payload, list) or (payload and not isinstance(payload[-1], dict)):
                self._record_malformed_basis(response, path, type(payload).__name__)
                return None
            if attempt + 1 < self._retry_attempts:
                self._sleeper(self._retry_delay_seconds * (attempt + 1))
        return None

    @staticmethod
    def _record_malformed_basis(response: httpx.Response, path: str, payload_type: str) -> None:
        error_code, error_message = _binance_error_identity(response)
        logger.warning(
            "Binance basis returned an unexpected payload; recording MISSING_DATA "
            "path=%s status=%s payload_type=%s code=%s message=%s "
            "content_type=%s used_weight=%s",
            path,
            response.status_code,
            payload_type,
            error_code,
            error_message,
            response.headers.get("content-type", "unknown")[:100],
            _used_weight_summary(response),
        )

    def _defer_basis_after_rate_limit(self, response: httpx.Response, now: datetime) -> None:
        error_code, error_message = _binance_error_identity(response)
        parsed_until = _ban_until(error_message)
        maximum_until = now + timedelta(seconds=self._maximum_rate_limit_cooldown_seconds)
        if parsed_until is not None and parsed_until > now:
            self._basis_blocked_until = min(parsed_until, maximum_until)
            deadline_source = "payload"
            retry_after_seconds: float | None = None
        else:
            retry_after_seconds = _retry_after_seconds(
                response,
                fallback=self._rate_limit_fallback_seconds,
                maximum=self._maximum_rate_limit_cooldown_seconds,
            )
            self._basis_blocked_until = now + timedelta(seconds=retry_after_seconds)
            deadline_source = (
                "retry_after" if response.headers.get("Retry-After") is not None else "fallback"
            )
        logger.warning(
            "Binance basis rate limited; recording MISSING_DATA "
            "status=%s blocked_until=%s deadline_source=%s retry_after_seconds=%s "
            "code=%s message=%s "
            "used_weight=%s",
            response.status_code,
            self._basis_blocked_until.isoformat(),
            deadline_source,
            retry_after_seconds,
            error_code,
            error_message,
            _used_weight_summary(response),
        )


def _decimal(payload: dict[str, Any], field: str) -> Decimal:
    try:
        value = Decimal(str(payload[field]))
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError(f"invalid Binance derivatives field: {field}") from error
    if not value.is_finite():
        raise ValueError(f"non-finite Binance derivatives field: {field}")
    return value


def _integer(payload: dict[str, Any], field: str) -> int:
    try:
        return int(payload[field])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError(f"invalid Binance derivatives timestamp: {field}") from error


def _retry_after_seconds(response: httpx.Response, *, fallback: float, maximum: float) -> float:
    raw_value = response.headers.get("Retry-After")
    if raw_value is not None:
        try:
            parsed = float(raw_value)
        except ValueError:
            parsed = fallback
        if isfinite(parsed) and parsed >= 0:
            return min(parsed, maximum)
    return fallback


def _ban_until(message: str) -> datetime | None:
    match = _BAN_UNTIL_PATTERN.search(message)
    if match is None:
        return None
    raw_timestamp = int(match.group(1))
    timestamp_seconds = (
        raw_timestamp / 1000 if raw_timestamp >= 1_000_000_000_000 else raw_timestamp
    )
    try:
        return datetime.fromtimestamp(timestamp_seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _binance_error_identity(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "unknown", "unparseable response"
    if not isinstance(payload, dict):
        return "unknown", "unexpected response"
    code = str(payload.get("code", "unknown"))[:64]
    message = str(payload.get("msg", "unknown"))[:200]
    return code, message


def _used_weight_summary(response: httpx.Response) -> str:
    values = tuple(
        f"{name}={value}"
        for name, value in response.headers.items()
        if name.lower().startswith("x-mbx-used-weight")
    )
    return ",".join(values) if values else "unknown"
