"""Capture current Upbit membership without inventing pre-observation history."""

from datetime import UTC, datetime

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.domain.universe import UniverseMember, UniverseSnapshot
from investment.crypto.infrastructure.universe_storage import UniverseSnapshotStorage
from investment.crypto.infrastructure.upbit import UpbitPublicClient


class CryptoUniverseService:
    def __init__(
        self, client: UpbitPublicClient, storage: UniverseSnapshotStorage
    ) -> None:
        self._client = client
        self._storage = storage

    def capture_current(self, observed_at: datetime | None = None) -> UniverseSnapshot:
        observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
        members = []
        for record in self._client.list_markets():
            if not record.market.startswith("KRW-"):
                continue
            quote, base = record.market.split("-", maxsplit=1)
            pair = build_universe((f"{base}/{quote}",)).pairs[0]
            members.append(UniverseMember(pair, record.warning, "upbit", observed))
        snapshot = UniverseSnapshot(
            observed,
            "upbit",
            tuple(sorted(members, key=lambda member: member.pair.symbol)),
        )
        self._storage.save(snapshot)
        return snapshot

    def latest(self) -> UniverseSnapshot:
        history = self._storage.load()
        if not history.snapshots:
            raise FileNotFoundError("no universe snapshot has been captured")
        return history.snapshots[-1]
