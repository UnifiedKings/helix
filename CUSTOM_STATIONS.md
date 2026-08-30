# Custom station providers

Helix supports custom station types as trusted Python plugins. A plugin can define its own recommendation logic, expose tuning controls in the Helix UI, inspect recent station/listening context, and optionally tell Helix how to generate the station's cover art.

Custom station plugins run **inside the Helix container with the same Python permissions as Helix itself**. Only install plugins you wrote yourself or fully trust.

### Enable custom stations

Mount a directory containing your plugin files:

```yaml
volumes:
  - ./custom_stations:/data/plugins/stations
```

Then enable plugin loading:

```env
HELIX_ENABLE_CUSTOM_STATION_TYPES=true
HELIX_CUSTOM_STATION_TYPES_DIR=/data/plugins/stations
```

`HELIX_CUSTOM_STATION_TYPES_DIR` may contain multiple directories separated by the platform path separator, but the normal Docker setup uses `/data/plugins/stations`.

Helix loads every `*.py` file in the configured directory except files whose names begin with `_`.

After adding or changing a plugin, restart Helix or reload the station providers.

### Minimal working plugin

A custom provider subclasses `StationProvider`, defines the provider identity fields, implements `next_tracks()`, and exports an instance so the loader can discover it.

```python
from __future__ import annotations

from app.station_providers.base import StationProvider
from app.station_providers.models import StationContext, StationResult


class ExampleStationProvider(StationProvider):
    station_type = "example_station"
    display_name = "Example Station"
    description = "A minimal custom Helix station."
    version = "1.0.0"
    builtin = False

    async def next_tracks(
        self,
        context: StationContext,
        count: int,
    ) -> list[StationResult]:
        return [
            StationResult(
                title="Example Song",
                artist="Example Artist",
                reason="Example custom station recommendation",
            )
        ][:count]


STATION_PROVIDER = ExampleStationProvider()
```

The module-level `STATION_PROVIDER` export is important. Defining the class by itself is not enough for Helix to discover the plugin.

### Required provider attributes

Every provider must define:

```python
station_type = "my_station"
display_name = "My Station"
description = "What this station does."
```

The loader validates these when the plugin is loaded.

`station_type`:

- must be unique
- should be stable once users have created stations with it
- may contain letters, numbers, `_`, `-`, and `.`
- is stored with saved stations, so changing it later effectively creates a different station type

`display_name` and `description` are shown to users in the Helix UI.

The following attributes are supported but have defaults:

```python
version = "1.0.0"
builtin = False
```

Custom plugins are always registered as non-built-in providers even if the module sets `builtin` differently.

### Required function: `next_tracks()`

This is the only abstract provider method that custom station logic must implement:

```python
async def next_tracks(
    self,
    context: StationContext,
    count: int,
) -> list[StationResult]:
    ...
```

Helix calls `next_tracks()` whenever it needs more station candidates.

- `context` is read-only information about the current station, queue, and recent listening history.
- `count` is how many recommendations Helix is asking the provider to return.
- Return up to `count` `StationResult` objects.
- Return results in playback preference order. Index `0` is the best/next candidate.
- Returning fewer than `count` is allowed.
- Returning an empty list means the provider currently has no recommendations.

Helix resolves returned artist/title recommendations through its normal playback/library pipeline. A custom station provider should recommend tracks; it should **not** directly modify Helix's queue, database, player state, download manager, or music-library filesystem.

`StationProvider` also supplies:

```python
async def next_track(self, context: StationContext) -> StationResult
```

You normally do **not** override this. The base implementation calls `next_tracks(context, 1)` and raises `StationNoResultError` if no result is returned.

### `StationContext`

`next_tracks()` receives a `StationContext` containing:

```python
context.user_id
context.station_id
context.station_name
context.station_type
context.config
context.recent_tracks
context.recent_artists
context.queued_tracks
context.already_selected
```

#### `context.config`

A dictionary containing the saved settings for this station instance:

```python
artist_cooldown = int(context.config.get("artist_cooldown", 5))
```

#### `context.recent_tracks`

Recent listening history. Each item is a `StationHistorySnapshot` with:

```python
row.title
row.artist
row.album
row.source
```

#### `context.recent_artists`

A recent artist-name list supplied by Helix.

#### `context.queued_tracks`

Tracks already in the current queue. Each `StationQueueSnapshot` contains:

```python
row.title
row.artist
row.album
row.source
row.position
```

Use this to avoid recommending something that Helix has already queued.

#### `context.already_selected`

`StationResult` objects already selected during the current generation batch. Providers should include this when applying anti-repeat logic so one request does not return the same track or artist repeatedly.

#### `context.recent_pairs()`

A convenience helper:

```python
blocked = context.recent_pairs()
```

It returns normalized `title|artist` keys for the recent-history tracks.

### Returning `StationResult`

A recommendation is returned as:

```python
StationResult(
    title="Song Title",
    artist="Artist Name",
    album="Album Name",
    duration_ms=240000,
    reason="Why the provider chose this track",
    confidence=1.0,
    provider_metadata={
        "video_id": "optional-source-id",
        "thumbnail_url": "optional-art-url",
    },
)
```

Only `title` and `artist` are required for a normal source-neutral recommendation.

Supported fields:

| Field | Purpose |
| --- | --- |
| `title` | Track title. |
| `artist` | Track artist. |
| `album` | Optional album name. |
| `duration_ms` | Optional duration in milliseconds. |
| `reason` | Optional user/debug-facing explanation for the recommendation. |
| `confidence` | Optional provider confidence value; defaults to `1.0`. |
| `provider_metadata` | Optional source/provider-specific metadata. |

`StationResult.key()` returns Helix's normalized `title|artist` key for the result.

If your provider already knows a precise external source identifier, it may include that information in `provider_metadata`. Built-in YouTube Music-backed providers, for example, can include `video_id` and `thumbnail_url`. Keep the top-level result source-neutral whenever possible.

### Optional function: `config_options()`

Implement `config_options()` when the station should expose editable settings in Helix:

```python
from app.station_providers.models import StationConfigOption


def config_options(self) -> list[StationConfigOption]:
    return [
        StationConfigOption(
            key="artist_cooldown",
            label="No repeated artist within",
            type="integer",
            description="Do not reuse an artist until this many tracks have passed.",
            default=5,
            min_value=0,
            max_value=25,
            step=1,
        ),
        StationConfigOption(
            key="include_deep_cuts",
            label="Include deep cuts",
            type="boolean",
            description="Allow tracks outside the most popular songs.",
            default=True,
        ),
    ]
```

Helix currently supports these option types:

```text
string
number
integer
boolean
select
multiselect
textarea
artist_search
track_search
```

`artist_search` and `track_search` render the same YouTube Music search-and-select widgets used by Helix's built-in stations. Plugin authors normally create them with the convenience helpers instead of constructing the option manually:

```python
from app.station_providers import seed_artist_search, seed_track_search

def config_options(self):
    return [
        seed_artist_search(
            4,
            key="seed_artists",
            label="Seed artists",
            description="Choose up to four artists for this station.",
        ),
        seed_track_search(
            8,
            key="seed_tracks",
            label="Reference tracks",
            required=False,
            description="Optional tracks that influence this station.",
        ),
    ]
```

The first argument is the maximum number of selections allowed. Both helpers save a list in `context.config`.

Artist selections look like:

```python
[
    {
        "name": "Lord Huron",
        "browse_id": "UC...",
        "art_url": "https://...",
        "thumbnail_url": "https://...",
    }
]
```

Track selections look like:

```python
[
    {
        "title": "The World Ender",
        "artist": "Lord Huron",
        "album": "Strange Trails",
        "video_id": "...",
        "art_url": "https://...",
        "thumbnail_url": "https://...",
    }
]
```

Treat artwork and source IDs as optional metadata; `name` for artists and `title`/`artist` for tracks are the stable user-facing values a plugin should fall back to.

A `StationConfigOption` can define:

```python
key
label
type
description
required
default
min_value
max_value
step
choices
min_items
max_items
```

`min_items` and `max_items` apply to `artist_search` and `track_search`. The `seed_artist_search()` and `seed_track_search()` helpers set these automatically.

For `select` or `multiselect`, choices use dictionaries such as:

```python
choices=[
    {"value": "focused", "label": "Focused"},
    {"value": "wide", "label": "Wide"},
]
```

The frontend builds the station creation/editing UI from these definitions, so plugin-specific settings do not require frontend code changes.

### Optional function: `validate_config()`

The base provider automatically validates `required=True` options.

Override `validate_config()` when you need additional or cross-field validation:

```python
def validate_config(self, config: dict) -> None:
    super().validate_config(config)

    minimum = int(config.get("minimum_year", 1990))
    maximum = int(config.get("maximum_year", 1999))

    if minimum > maximum:
        raise ValueError("Minimum year cannot be greater than maximum year")
```

Always call:

```python
super().validate_config(config)
```

if you still want the standard required-field validation.

### Optional function: `cover_hint()`

Providers can tell Helix how the generated station artwork should be built:

```python
def cover_hint(self, config: dict) -> dict | None:
    return {
        "mode": "artists",
        "artists": [
            "Nirvana",
            "Pearl Jam",
            "The Smashing Pumpkins",
            "Radiohead",
        ],
        "fallback_seed": "90s Rock",
    }
```

User-uploaded station covers always take priority over generated artwork.

For generated art, Helix currently supports these modes:

#### `track`

Use artwork associated with a representative track:

```python
return {
    "mode": "track",
    "title": "Song Title",
    "artist": "Artist Name",
    "album": "Optional Album Name",
    "fallback_seed": "Station Name",
}
```

Helix tries local/Subsonic artwork first, then YouTube Music artwork, then falls back to generated artwork.

#### `artist`

Build the normal station collage from one representative artist:

```python
return {
    "mode": "artist",
    "artist": "Artist Name",
    "fallback_seed": "Station Name",
}
```

Helix tries local album art first and fills missing artwork from YouTube Music before using generated tiles.

#### `artists`

Build a collage from multiple representative artists:

```python
return {
    "mode": "artists",
    "artists": [
        "Artist One",
        "Artist Two",
        "Artist Three",
        "Artist Four",
    ],
    "fallback_seed": "Station Name",
}
```

This is useful for genre, era, mood, or other stations that do not have one meaningful seed artist.

#### `album`

Request an album-style cover strategy:

```python
return {
    "mode": "album",
    "artist": "Artist Name",
    "fallback_seed": "Station Name",
}
```

The current renderer resolves this through the declared artist and degrades to the normal artwork fallbacks if needed.

#### `generated`

Skip external artwork and use Helix's generated fallback:

```python
return {
    "mode": "generated",
    "label": "My Station",
    "fallback_seed": "My Station",
}
```

Plugins that do not implement `cover_hint()` remain compatible. Helix derives a generic cover strategy from the station's saved seed/config where possible.

### Registering the provider

Every plugin module must expose providers using **one** of the following supported mechanisms.

#### One provider

```python
STATION_PROVIDER = ExampleStationProvider()
```

#### Multiple providers

```python
STATION_PROVIDERS = [
    FirstProvider(),
    SecondProvider(),
]
```

#### Registration function

```python
def register_station_providers():
    return [
        FirstProvider(),
        SecondProvider(),
    ]
```

The registration function may also return a single `StationProvider`.

If none of these exports exist, Helix will load the Python file but log:

```text
Custom station provider file registered no providers
```

and no station type will appear in the UI.

### Complete example

This example demonstrates all supported provider hooks without directly mutating Helix state:

```python
from __future__ import annotations

import random
from typing import Any

from app.station_providers.base import StationProvider
from app.station_providers.models import (
    StationConfigOption,
    StationContext,
    StationResult,
)


CATALOG = {
    "Artist One": ["Track A", "Track B", "Track C"],
    "Artist Two": ["Track D", "Track E", "Track F"],
}


class ExampleRadioProvider(StationProvider):
    station_type = "example_radio"
    display_name = "Example Radio"
    description = "Example custom radio with configurable anti-repeat behavior."
    version = "1.0.0"
    builtin = False

    def config_options(self) -> list[StationConfigOption]:
        return [
            StationConfigOption(
                key="artist_cooldown",
                label="No repeated artist within",
                type="integer",
                description="Avoid artists used in the most recent N tracks.",
                default=3,
                min_value=0,
                max_value=20,
                step=1,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> None:
        super().validate_config(config)

        cooldown = int(config.get("artist_cooldown", 3))
        if cooldown < 0:
            raise ValueError("Artist cooldown cannot be negative")

    def cover_hint(self, config: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "mode": "artists",
            "artists": list(CATALOG.keys())[:4],
            "fallback_seed": self.display_name,
        }

    async def next_tracks(
        self,
        context: StationContext,
        count: int,
    ) -> list[StationResult]:
        cooldown = max(0, int(context.config.get("artist_cooldown", 3)))

        # Exact tracks already heard or queued.
        blocked_tracks = set(context.recent_pairs())
        blocked_tracks.update(
            f"{row.title.strip().lower()}|{row.artist.strip().lower()}"
            for row in context.queued_tracks
        )
        blocked_tracks.update(result.key() for result in context.already_selected)

        # Artists used immediately before the next pick.
        recent_artist_sequence = [
            result.artist
            for result in reversed(context.already_selected)
        ]
        recent_artist_sequence += [
            row.artist
            for row in reversed(context.queued_tracks)
        ]
        recent_artist_sequence += [
            row.artist
            for row in context.recent_tracks
        ]
        blocked_artists = {
            artist.strip().lower()
            for artist in recent_artist_sequence[:cooldown]
            if artist.strip()
        }

        candidates: list[StationResult] = []
        for artist, titles in CATALOG.items():
            if artist.lower() in blocked_artists:
                continue

            for title in titles:
                result = StationResult(
                    title=title,
                    artist=artist,
                    reason="Example custom radio",
                )
                if result.key() in blocked_tracks:
                    continue
                candidates.append(result)

        random.shuffle(candidates)
        return candidates[: max(0, int(count))]


STATION_PROVIDER = ExampleRadioProvider()
```

The example uses a tiny in-file catalog only to demonstrate the plugin API. A real dynamic station can call an external recommendation service or Helix's available integration helpers and return discovered `StationResult` objects at runtime.

### Using Helix integration helpers

Custom plugins run inside the Helix Python environment and may import available read-only integration helpers. For example, a YouTube Music-backed plugin can currently import helpers from:

```python
from app.integrations.ytmusic import ...
```

This is useful for dynamic custom stations such as genre/era radios.

Those helper functions are part of Helix's internal integration layer rather than the minimal `StationProvider` contract, so plugin authors should expect them to evolve more often than `StationProvider`, `StationContext`, `StationResult`, and `StationConfigOption`.

Regardless of which discovery service you use, custom providers should not directly mutate:

- database sessions or Helix models
- player state
- queue state
- download/finalization workers
- library filesystem paths

Return recommendations and let Helix handle resolution, queueing, playback, fulfillment, and metadata repair.

### Troubleshooting custom stations

If a plugin does not appear, check the container logs.

Common causes include:

- `HELIX_ENABLE_CUSTOM_STATION_TYPES` is not `true`
- the plugin directory is not mounted into the container
- the `.py` filename begins with `_`
- the module defines a provider class but forgets `STATION_PROVIDER`, `STATION_PROVIDERS`, or `register_station_providers()`
- `station_type`, `display_name`, or `description` is missing
- two providers use the same `station_type`
- a `StationConfigOption` is missing its key, label, or type
- the plugin raises an exception while being imported

Useful log messages include:

```text
Loaded custom station provider: <station_type>
```

and:

```text
Custom station provider file registered no providers
```

or:

```text
Failed to load custom station provider from <path>
```

## Organizing tuning options into categories

Each `StationConfigOption` can announce where it belongs in the tuning UI. Helix groups options with the same `category` and renders the categories in `category_order` order. Category IDs should be stable machine-friendly strings; `category_label` controls the user-facing label.

```python
StationConfigOption(
    key="artist_cooldown",
    label="No repeated artist within",
    type="integer",
    default=5,
    category="behavior",
    category_label="Behavior",
    category_order=30,
    order=10,
)
```

Search helpers accept the same metadata:

```python
seed_artist_search(
    25,
    key="seed_artists",
    category="seeds",
    category_label="Seeds",
    category_order=10,
)
```

Recommended category IDs are `seeds`, `discovery`, `behavior`, `playback`, and `advanced`, but custom providers may use other IDs. Providers that do not announce categories remain compatible and are placed in a generic `Options` category.
