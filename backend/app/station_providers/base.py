from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import StationConfigOption, StationContext, StationResult


class StationProviderError(Exception):
    """Base exception for StationProvider failures."""


class StationNoResultError(StationProviderError):
    """Raised when a provider cannot return any tracks."""


class StationProvider(ABC):
    """Base contract for all built-in and custom station providers.

    A provider may call external recommendation services, but it must not directly
    mutate Helix state or access Helix internals like DB sessions, queue objects,
    download workers, filesystem library paths, or player state. It receives a
    read-only StationContext and returns ordered StationResult recommendations.
    """

    station_type: str
    display_name: str
    description: str
    version: str = "1.0.0"
    builtin: bool = False

    def config_options(self) -> list[StationConfigOption]:
        return []

    def cover_hint(self, config: dict[str, Any]) -> dict[str, Any] | None:
        """Optionally describe how Helix should build this station's generated cover.

        Supported modes are currently ``track``, ``album``, ``artist``,
        ``artists``, and ``generated``. Custom providers may override this
        method; providers that do not are handled by Helix's generic fallback
        strategy so existing plugins remain compatible.
        """
        return None

    def validate_config(self, config: dict[str, Any]) -> None:
        """Validate provider-specific config.

        Base validation checks required fields from config_options(). Providers
        can override and call super() for cross-field validation.
        """
        for opt in self.config_options():
            value = config.get(opt.key)
            empty = (
                value is None
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, (list, tuple, set, dict)) and len(value) == 0)
            )
            if opt.required and empty:
                raise ValueError(f"{opt.label or opt.key} is required")

            if opt.type not in {"artist_search", "track_search"} or empty:
                continue
            if not isinstance(value, list):
                raise ValueError(f"{opt.label or opt.key} must be a list of selected items")
            if opt.min_items is not None and len(value) < int(opt.min_items):
                raise ValueError(f"{opt.label or opt.key} requires at least {int(opt.min_items)} selection(s)")
            if opt.max_items is not None and len(value) > int(opt.max_items):
                raise ValueError(f"{opt.label or opt.key} allows at most {int(opt.max_items)} selection(s)")

    @abstractmethod
    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        """Return up to count ordered recommendations.

        Helix calls this to satisfy the configured station prefetch depth. The
        returned order is meaningful: index 0 is the next immediate track.
        """

    async def next_track(self, context: StationContext) -> StationResult:
        results = await self.next_tracks(context, 1)
        if not results:
            raise StationNoResultError(f"{self.station_type} returned no tracks")
        return results[0]
