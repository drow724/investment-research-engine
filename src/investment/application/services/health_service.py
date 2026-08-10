"""Service metadata use case."""

from dataclasses import dataclass

from investment import __version__


@dataclass(frozen=True, slots=True)
class HealthStatus:
    status: str
    service: str
    version: str


class HealthService:
    def check(self) -> HealthStatus:
        return HealthStatus(
            status="UP", service="investment-research-engine", version=__version__
        )
