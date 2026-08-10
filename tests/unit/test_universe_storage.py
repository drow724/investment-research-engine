from datetime import UTC, datetime

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.domain.universe import UniverseMember, UniverseSnapshot
from investment.crypto.infrastructure.universe_storage import UniverseSnapshotStorage


def test_universe_snapshot_storage_round_trip(tmp_path) -> None:
    observed = datetime(2025, 1, 1, tzinfo=UTC)
    pairs = build_universe(("BTC/KRW", "ETH/KRW")).pairs
    snapshot = UniverseSnapshot(
        observed,
        "upbit",
        tuple(UniverseMember(pair, False, "upbit", observed) for pair in pairs),
    )
    storage = UniverseSnapshotStorage(tmp_path)
    first_path = storage.save(snapshot)
    second_path = storage.save(snapshot)
    history = storage.load()
    assert first_path == second_path
    assert history.snapshots == (snapshot,)
