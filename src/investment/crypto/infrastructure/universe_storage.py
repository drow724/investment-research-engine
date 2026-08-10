"""Append-only JSON snapshots for point-in-time universe membership."""

import json
from datetime import datetime
from pathlib import Path

from investment.crypto.domain.market import parse_trading_pair
from investment.crypto.domain.universe import UniverseHistory, UniverseMember, UniverseSnapshot


class UniverseSnapshotStorage:
    def __init__(self, root: str | Path = "data/normalized/crypto/universe") -> None:
        self.root = Path(root)

    def save(self, snapshot: UniverseSnapshot) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = snapshot.observed_at.strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.root / f"{name}.json"
        payload = {
            "observed_at": snapshot.observed_at.isoformat(),
            "source": snapshot.source,
            "members": [
                {"pair": member.pair.symbol, "warning": member.warning}
                for member in snapshot.members
            ],
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def load(self) -> UniverseHistory:
        snapshots = [self._read(path) for path in sorted(self.root.glob("*.json"))]
        return UniverseHistory(tuple(snapshots))

    @staticmethod
    def _read(path: Path) -> UniverseSnapshot:
        payload = json.loads(path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(payload["observed_at"])
        source = str(payload["source"])
        members = []
        for item in payload["members"]:
            symbol = str(item["pair"])
            # Stored symbols are concatenated. The initial persisted universe is KRW quoted.
            if not symbol.endswith("KRW"):
                raise ValueError(f"unsupported stored universe pair: {symbol}")
            base = symbol.removesuffix("KRW")
            pair = parse_trading_pair(f"{base}/KRW")
            members.append(
                UniverseMember(pair, bool(item["warning"]), source, observed_at)
            )
        return UniverseSnapshot(observed_at, source, tuple(members))
