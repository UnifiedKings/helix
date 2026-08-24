from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from types import ModuleType
from typing import Iterable

from .base import StationProvider
from .models import StationConfigOption, StationProviderInfo
from .builtins.similar_artist import SimilarArtistProvider
from .builtins.artist_collection import ArtistCollectionProvider
from .builtins.tag_radio import TagRadioProvider
from .builtins.song_radio import SongRadioProvider

LOG = logging.getLogger("helix.station_providers")

DEFAULT_STATION_TYPE = "similar_artist"
LEGACY_STATION_TYPE_ALIASES = {
    "listenbrainz_similar_artist": "similar_artist",
}

def _subsonic_configured_from_env_or_settings() -> bool:
    """Best-effort check for UI option exposure.

    Station provider metadata is not user-specific, so read the global settings
    directly and hide Library-only mode when Subsonic is not configured.
    """
    try:
        from ..db import SessionLocal
        from ..settings_store import get_settings

        db = SessionLocal()
        try:
            settings = get_settings(db)
            return bool(
                str(settings.get("subsonic_base_url") or "").strip()
                and str(settings.get("subsonic_username") or "").strip()
                and str(settings.get("subsonic_password") or "").strip()
            )
        finally:
            db.close()
    except Exception:
        return False


def _source_mode_option() -> StationConfigOption:
    choices = [{"value": "prefer_library", "label": "Prefer library, allow discovery"}]
    if _subsonic_configured_from_env_or_settings():
        choices.append({"value": "library_only", "label": "Library only"})
    return StationConfigOption(
        key="source_mode",
        label="Track source",
        type="select",
        description="Choose whether this station may use discovery tracks, or only songs already present in your Subsonic library.",
        default="prefer_library",
        choices=choices,
    )

_PROVIDERS: dict[str, StationProvider] = {}
_LOADED = False


def _validate_provider(provider: StationProvider) -> None:
    station_type = str(getattr(provider, "station_type", "") or "").strip()
    if not station_type:
        raise ValueError("station_type is required")
    if not station_type.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ValueError(f"station_type contains unsupported characters: {station_type!r}")
    if not str(getattr(provider, "display_name", "") or "").strip():
        raise ValueError(f"display_name is required for {station_type}")
    if not str(getattr(provider, "description", "") or "").strip():
        raise ValueError(f"description is required for {station_type}")
    # Force config option materialization now so broken plugins fail at load time.
    for opt in provider.config_options():
        if not opt.key or not opt.label or not opt.type:
            raise ValueError(f"invalid config option for {station_type}: {opt!r}")


def register_station_provider(provider: StationProvider) -> None:
    _validate_provider(provider)
    station_type = provider.station_type.strip()
    if station_type in _PROVIDERS:
        raise ValueError(f"duplicate station provider: {station_type}")
    _PROVIDERS[station_type] = provider
    LOG.info("Loaded %s station provider: %s", "built-in" if provider.builtin else "custom", station_type)


def _load_builtin_providers() -> None:
    register_station_provider(SimilarArtistProvider())
    register_station_provider(ArtistCollectionProvider())
    register_station_provider(TagRadioProvider())
    register_station_provider(SongRadioProvider())


def _custom_plugins_enabled() -> bool:
    raw = str(os.getenv("HELIX_ENABLE_CUSTOM_STATION_TYPES", "false") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _plugin_dirs() -> list[Path]:
    raw = str(os.getenv("HELIX_CUSTOM_STATION_TYPES_DIR", "/data/plugins/stations") or "").strip()
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def _load_module_from_path(path: Path) -> ModuleType:
    module_name = f"helix_custom_station_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _providers_from_module(module: ModuleType) -> Iterable[StationProvider]:
    register_fn = getattr(module, "register_station_providers", None)
    if callable(register_fn):
        providers = register_fn()
        if isinstance(providers, StationProvider):
            return [providers]
        return list(providers or [])

    provider = getattr(module, "STATION_PROVIDER", None)
    if isinstance(provider, StationProvider):
        return [provider]

    providers = getattr(module, "STATION_PROVIDERS", None)
    if providers:
        return list(providers)

    return []


def _load_custom_providers() -> None:
    if not _custom_plugins_enabled():
        LOG.info("Custom station providers disabled. Set HELIX_ENABLE_CUSTOM_STATION_TYPES=true to enable.")
        return

    for plugin_dir in _plugin_dirs():
        if not plugin_dir.exists():
            LOG.info("Custom station provider directory does not exist: %s", plugin_dir)
            continue
        for path in sorted(plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = _load_module_from_path(path)
                providers = _providers_from_module(module)
                if not providers:
                    LOG.warning("Custom station provider file registered no providers: %s", path)
                    continue
                for provider in providers:
                    provider.builtin = False
                    register_station_provider(provider)
            except Exception:
                LOG.exception("Failed to load custom station provider from %s", path)


def reload_station_providers() -> None:
    global _LOADED
    _PROVIDERS.clear()
    _load_builtin_providers()
    _load_custom_providers()
    _LOADED = True


def _ensure_loaded() -> None:
    if not _LOADED:
        reload_station_providers()



def canonical_station_type(station_type: str | None) -> str:
    key = (station_type or DEFAULT_STATION_TYPE).strip() or DEFAULT_STATION_TYPE
    return LEGACY_STATION_TYPE_ALIASES.get(key, key)


def get_station_provider(station_type: str | None) -> StationProvider:
    _ensure_loaded()
    key = canonical_station_type(station_type)
    provider = _PROVIDERS.get(key)
    if provider:
        return provider
    LOG.warning("Unknown station provider %r; falling back to %s", key, DEFAULT_STATION_TYPE)
    return _PROVIDERS[DEFAULT_STATION_TYPE]


def _with_common_options(provider: StationProvider) -> list[StationConfigOption]:
    options = list(provider.config_options())
    keys = {str(opt.key or "") for opt in options}
    if "source_mode" not in keys:
        options.insert(0, _source_mode_option())
    return options


def list_station_providers() -> list[StationProviderInfo]:
    _ensure_loaded()
    providers = sorted(_PROVIDERS.values(), key=lambda p: (not p.builtin, p.display_name.lower()))
    return [
        StationProviderInfo(
            station_type=p.station_type,
            display_name=p.display_name,
            description=p.description,
            version=getattr(p, "version", "1.0.0") or "1.0.0",
            builtin=bool(getattr(p, "builtin", False)),
            config_options=_with_common_options(p),
        )
        for p in providers
    ]
