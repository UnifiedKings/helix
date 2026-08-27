# Helix

Helix is a self-hosted music player, discovery tool, and shared listening system built around a Subsonic-compatible music library.

It is meant for people who run their own music server but still want a more modern experience: search, queues, generated stations, playlists, shared listening, and optional fulfillment for tracks that are not already in the library.

Helix is still early software. It is usable, but expect rough edges, breaking changes, and setup work.

## Features

- Search and play music from a Subsonic-compatible library
- Play tracks, albums, playlists, and generated stations
- Create stations from artists, artist collections, and tags
- Run shared listening lobbies with guest queue and playback controls
- Join lobbies with simple 5-letter codes and optional password protection
- Mirror a Helix lobby into Discord voice with the optional [HelixBot](https://github.com/UnifiedKings/helixbot) companion project
- Add missing music to your library through optional fulfillment providers
- Repair metadata before finalized imports
- Use custom station providers through a plugin system
- Continue working with or without a configured Subsonic server, with unsupported features hidden or disabled

## Demo

### Search

Search across your configured music sources and jump directly into playback, albums, artists, or queue actions.

![Search demo](docs/gifs/search.gif)

### Stations

Create a generated station from the music you want to build around.

![Station creation demo](docs/gifs/station_creation.gif)

Start a station and let Helix begin filling the queue.

![Station playback demo](docs/gifs/station_play.gif)

Tune supported stations without rebuilding them from scratch.

![Station tuning demo](docs/gifs/station_tune.gif)

Stations continue filling ahead of playback automatically.

![Station queue autofill demo](docs/gifs/queue_station_autofill.gif)

### Queue control

Add a result to the end of the current queue:

![Add to end of queue demo](docs/gifs/add_to_queue_end.gif)

Or insert it as the next track to play:

![Play next demo](docs/gifs/add_to_queue_next.gif)

Queue items can also be reordered directly:

![Queue reordering demo](docs/gifs/queue_order_changing.gif)

### Add to Subsonic

If fulfillment is enabled, missing music can be requested from Helix and added back into the configured Subsonic library.

![Add to Subsonic demo](docs/gifs/add_to_subsonic.gif)

After fulfillment and metadata repair, the imported music appears in the library:

![Imported album proof](docs/gifs/add_to_subsonic_proof.gif)

Additional library views:

![Imported album library view](docs/gifs/add_to_subsonic_proof_2.png)

![Imported album track view](docs/gifs/add_to_subsonic_proof_3.png)

### Shared lobbies

Create a lobby and share its 5-letter join code with other listeners.

![Lobby creation demo](docs/gifs/lobby_creation.gif)

Lobby hosts can configure guest permissions and other lobby behavior.

![Lobby settings](docs/gifs/lobby_settings.png)

Stations can be started directly inside a lobby and will populate its shared queue.

![Lobby station demo](docs/gifs/lobby_stations.gif)

Lobbies maintain synchronized playback state across connected clients.

## How it works

Helix has three main parts:

- **Backend**: Python/FastAPI service for playback state, queues, stations, fulfillment, metadata repair for fulfillment, lobbies, and Subsonic communication.
- **Frontend**: Web UI for search, playback, stations, playlists, lobbies, and settings.
- **Music library**: A Subsonic-compatible server such as Navidrome.

Helix is built with Subsonic in mind. Helix can search it, stream from it, and optionally add requested tracks/albums back into it.

## Important behavior

Helix is designed to be conservative with fulfillment.

- Tracks are handled individually.
- Metadata is repaired before finalization.
- Library imports are controlled by your Beets configuration.
- Only what is requested is added to the Subsonic library.

## Quick start

The easiest way to run Helix is with Docker Compose.

Create a `docker-compose.yml`:

```yaml
services:
  helix:
    image: ghcr.io/unifiedkings/helix:latest
    container_name: helix
    ports:
      - "10011:8000"
    restart: unless-stopped

    volumes:
      # Helix persisted data: database, logs, stream cache, station covers, etc.
      - ./data:/data

      # Inbound download/fulfillment staging
      - ./inbound_yt:/inbound_yt

      # Optional: music library root used by Navidrome/Subsonic/beets.
      # Change this to your real music path, or remove it if you are not using
      # Helix fulfillment/import features.
      - /path/to/music:/data/music

      # Beets config used by Helix fulfillment/import.
      - ./beets_config:/data/helix/beets

      # Optional custom station providers. Disabled by default.
      - ./custom_stations:/data/plugins/stations

    environment:
      # Optional ListenBrainz token. Some station/discovery features may work
      # better with this configured.
      LISTENBRAINZ_TOKEN: ""

      # Optional Subsonic/Navidrome connection.
      # You can also configure these inside the Helix settings UI if supported.
      SUBSONIC_BASE_URL: ""
      SUBSONIC_USERNAME: ""
      SUBSONIC_PASSWORD: ""

      # Keep yt-dlp current independently of the Helix image.
      # Stable is the default; use "nightly" only if you need fixes not yet
      # available in the stable yt-dlp release.
      HELIX_YTDLP_AUTO_UPDATE: "true"
      HELIX_YTDLP_CHANNEL: "stable"

      # Station / queue prefetch
      HELIX_PREFETCH_AHEAD: "3"

      # Max tracks added when someone pastes a playlist/album link into a lobby
      HELIX_LOBBY_YT_LINK_MAX_ITEMS: "100"

      # Public-safe defaults
      HELIX_ENABLE_API_DOCS: "false"
      HELIX_ALLOW_NON_ADMIN_IMPORT: "false"

      # Custom station providers are trusted Python code.
      # Only enable this if you trust every .py file in ./custom_stations.
      HELIX_ENABLE_CUSTOM_STATION_TYPES: "false"
      HELIX_CUSTOM_STATION_TYPES_DIR: "/data/plugins/stations"

      # Optional DB watchdog tuning
      HELIX_DB_WATCHDOG_WARN_S: "2"
      HELIX_DB_WATCHDOG_ERROR_S: "10"
```

Start Helix:

```bash
docker compose up -d
```

Then open:

```text
http://localhost:10011
```

On first launch, Helix will ask you to create an admin account.

## HTTP vs HTTPS session cookies

Helix uses a secure session cookie by default:

```env
HELIX_COOKIE_SECURE=true
```

Keep this set to `true` when Helix is served over **HTTPS**.

If you are accessing Helix directly over plain HTTP on a trusted local network, such as:

```text
http://192.168.1.50:8000
```

set:

```env
HELIX_COOKIE_SECURE=false
```

Browsers do not send cookies marked `Secure` over plain HTTP. If `HELIX_COOKIE_SECURE=true` while using an `http://` URL, login or initial setup can appear to succeed, but the next authenticated request will return `401 Unauthorized` and Helix will immediately send you back to the login page.

For any internet-facing deployment, use HTTPS and leave `HELIX_COOKIE_SECURE=true`.

## yt-dlp updates

Helix relies on `yt-dlp` for YouTube-backed playback and fulfillment. YouTube changes frequently, and an outdated `yt-dlp` can cause playback or downloads to fail even when the rest of Helix is working normally.

To reduce that failure mode, the Docker image includes a bundled version of `yt-dlp` **and Helix checks for an updated version each time the container starts**. This happens before the Helix application starts, so users do not need to rebuild or pull a new Helix image solely to receive a newer `yt-dlp`.

The default settings are:

```env
HELIX_YTDLP_AUTO_UPDATE=true
HELIX_YTDLP_CHANNEL=stable
```

`HELIX_YTDLP_CHANNEL` supports:

- `stable` — the latest stable `yt-dlp` release. This is the default.
- `nightly` — allows pre-release/nightly builds when a YouTube fix has not reached stable yet.

Set `HELIX_YTDLP_AUTO_UPDATE=false` if you specifically want to use only the version bundled into the Helix image.

### If the yt-dlp update fails

A failed update **does not prevent Helix from starting**. Helix logs a warning and continues with the version already bundled in the container. This keeps temporary package-index or network outages from taking the entire service down.

However, if that bundled version has become too old for current YouTube behavior, YouTube-backed playback and downloads may fail until `yt-dlp` can be updated. If those features suddenly stop working, check the container startup logs for an entry beginning with `[helix-entrypoint]` before assuming the Helix application itself is broken. Restarting the container when network/package access is available will retry the update; pulling a current Helix image also refreshes the bundled fallback version.

Because the update check runs at container startup, restarts may take slightly longer while `pip` checks for or installs a newer `yt-dlp`.

## Subsonic / Navidrome

Helix is built around the Subsonic API. Navidrome is the main target, but other Subsonic-compatible servers may work.

If Subsonic is configured, Helix can:

- search your library
- stream library tracks
- detect whether a track already exists
- add fulfilled tracks back into the library
- support library-only station behavior

If Subsonic is not configured, Helix should disable library-dependent features instead of failing outright.

## Stations

Helix includes built-in station providers for several styles of radio:

- Similar artist radio
- Artist collection radio
- Tag radio

Stations can be tuned with provider-specific settings. Depending on the provider, this may include artist variety, discovery level, source mode, tag strictness, or repeat avoidance.

## Custom station providers

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
```

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
```

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

## Lobbies

Lobbies are shared listening sessions where multiple people can join the same queue and playback state.

Each lobby has a simple 5-letter join code and can optionally be password-protected. Hosts can share the code or a direct invite link with other listeners.

Depending on permissions, guests can:

- add tracks to the queue
- remove their own queued tracks
- control playback
- skip tracks
- seek within the current track
- paste supported music links into the lobby queue

Lobby playback state is synchronized between connected clients so everyone can listen along from the same position.

## HelixBot

[HelixBot](https://github.com/UnifiedKings/helixbot) is an optional companion project that connects a Helix lobby to a Discord voice channel.

HelixBot links a Discord server to a Helix lobby using its 5-letter join code, joins Discord voice, and mirrors the lobby's current playback. Helix remains the source of truth: the bot follows track changes, pause/resume, seeks, and queue advancement rather than exposing separate Discord playback controls.

HelixBot runs in its own Docker container and can be configured to connect to any reachable Helix instance.


## Security notes

Before exposing Helix outside your LAN, put it behind HTTPS and review your deployment. Keep `HELIX_COOKIE_SECURE=true` for HTTPS deployments; only disable it when intentionally running Helix over plain HTTP on a trusted network.

Custom station providers can execute Python code inside the Helix container. Keep them disabled unless you need them.

Recommended public defaults:

```env
HELIX_ENABLE_API_DOCS=false
HELIX_ALLOW_NON_ADMIN_IMPORT=false
HELIX_ENABLE_CUSTOM_STATION_TYPES=false
```

Unless you want to use the custom station feature.

## License

Helix is licensed under the [GNU Affero General Public License v3.0](LICENSE) (**AGPL-3.0-only**).

You may use, modify, and redistribute Helix under the terms of the AGPL. If you modify Helix and make that modified version available to users over a network, the corresponding source code must also be made available as required by the license.
