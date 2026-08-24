# Helix

Helix is a self-hosted music player, discovery tool, and shared listening system built around a Subsonic-compatible music library.

It is meant for people who run their own music server but still want a more modern experience: search, queues, generated stations, playlists, and optional fulfillment for tracks that are not already in the library.

Helix is still early software. It is usable, but expect rough edges, breaking changes, and setup work.

## Features

- Search and play music from a Subsonic-compatible library
- Play tracks, albums, playlists, and generated stations
- Create stations from artists, artist collections, and tags
- Run shared listening lobbies with guest queue controls
- Add missing music to your library through optional fulfillment providers
- Repair metadata before finalized imports
- Use custom station providers through a plugin system
- Continue working with or without a configured Subsonic server, with unsupported features hidden or disabled

## Demo

Add your GIFs to `docs/gifs/` and replace these placeholders as needed.

### Search and playback

![Search and playback demo](docs/gifs/search-playback.gif)

### Stations

![Station demo](docs/gifs/stations.gif)

### Shared lobbies

![Lobby demo](docs/gifs/lobbies.gif)

### Add to library

![Add to library demo](docs/gifs/add-to-library.gif)

## How it works

Helix has three main parts:

- **Backend**: Python/FastAPI service for playback state, queues, stations, fulfillment, metadata repair for fulfillment, lobbies, and Subsonic communication.
- **Frontend**: Web UI for search, playback, stations, playlists, lobbies, and settings.
- **Music library**: A Subsonic-compatible server such as Navidrome.

Helix is built with subsonic in mind. Helix can search it, stream from it, and optionally add requested tracks/albums back into it.

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

Helix supports custom station providers as Python plugins.

Plugin files can be placed in:

```text
/data/plugins/stations
```

Then enable plugin loading:

```env
HELIX_ENABLE_CUSTOM_STATION_TYPES=true
HELIX_CUSTOM_STATION_TYPES_DIR=/data/plugins/stations
```

Custom station providers are trusted code. They run inside the Helix container. Only enable plugins you wrote yourself or fully trust.

## Lobbies

Lobbies let other people join a shared listening session from an invite link.

Depending on permissions, guests can:

- add tracks to the queue
- remove their own queued tracks
- control playback
- skip tracks
- seek within the current track
- paste supported music links into the lobby queue


## Security notes

Before exposing Helix outside your LAN, put it behind HTTPS and review your deployment.

Custom station providers can execute Python code inside the Helix container. Keep them disabled unless you need them.

Recommended public defaults:

```env
HELIX_ENABLE_API_DOCS=false
HELIX_ALLOW_NON_ADMIN_IMPORT=false
HELIX_ENABLE_CUSTOM_STATION_TYPES=false
```

Unless you want to use the custom station feature.

## License

TBD
