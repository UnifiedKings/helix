from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StationConfigOptionType = Literal["string", "number", "integer", "boolean", "select", "multiselect", "textarea"]


@dataclass(frozen=True)
class StationConfigOption:
    """User-editable config option announced by a StationProvider.

    The frontend can render station create/edit forms from these definitions. The
    description is intentionally user-facing so plugin authors can explain what
    an option changes without hardcoding UI copy into Helix.
    """

    key: str
    label: str
    type: StationConfigOptionType
    description: str = ""
    required: bool = False
    default: Any = None
    min_value: float | int | None = None
    max_value: float | int | None = None
    step: float | int | None = None
    choices: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "default": self.default,
        }
        if self.min_value is not None:
            out["min"] = self.min_value
        if self.max_value is not None:
            out["max"] = self.max_value
        if self.step is not None:
            out["step"] = self.step
        if self.choices:
            out["choices"] = list(self.choices)
        return out


@dataclass(frozen=True)
class StationProviderInfo:
    station_type: str
    display_name: str
    description: str
    version: str
    builtin: bool
    config_options: list[StationConfigOption]

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_type": self.station_type,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "builtin": self.builtin,
            "config_options": [opt.to_dict() for opt in self.config_options],
        }


@dataclass(frozen=True)
class StationQueueSnapshot:
    title: str
    artist: str
    album: str = ""
    source: str = ""
    position: int = 0


@dataclass(frozen=True)
class StationHistorySnapshot:
    title: str
    artist: str
    album: str = ""
    source: str = ""


@dataclass(frozen=True)
class StationContext:
    """Read-only Helix context provided to station providers.

    Providers may use this data to make recommendations but must not mutate Helix
    state directly. Built-in providers and custom plugin providers receive the
    same constrained context.
    """

    user_id: str
    station_id: str
    station_name: str
    station_type: str
    config: dict[str, Any]
    recent_tracks: list[StationHistorySnapshot] = field(default_factory=list)
    recent_artists: list[str] = field(default_factory=list)
    queued_tracks: list[StationQueueSnapshot] = field(default_factory=list)
    already_selected: list["StationResult"] = field(default_factory=list)

    def recent_pairs(self) -> set[str]:
        return {f"{(t.title or '').strip().lower()}|{(t.artist or '').strip().lower()}" for t in self.recent_tracks}


@dataclass(frozen=True)
class StationResult:
    """A source-neutral track recommendation returned by a StationProvider."""

    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    reason: str = ""
    confidence: float = 1.0
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{(self.title or '').strip().lower()}|{(self.artist or '').strip().lower()}"
