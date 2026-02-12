from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class FulfillmentRequest:
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0


class FulfillmentProvider(Protocol):
    async def fulfill(self, req: FulfillmentRequest) -> Optional[str]:
        """Return an absolute path to a newly acquired audio file, or None if not fulfilled."""
        ...


class DisabledFulfillmentProvider:
    async def fulfill(self, req: FulfillmentRequest) -> Optional[str]:
        # Placeholder hook: user will plug in a provider later.
        return None
