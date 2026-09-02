# Helix

Helix is a self-hosted music player, discovery tool, and shared listening system built around a Subsonic-compatible music library.

It is meant for people who run their own music server but still want a more modern experience: search, queues, generated stations, playlists, shared listening, and optional fulfillment for tracks that are not already in the library.

Helix is still early software. It is usable, but expect rough edges, breaking changes, and setup work.

[![Join the Helix Discord](https://img.shields.io/badge/Discord-Join%20the%20Helix%20community-5865F2?logo=discord&logoColor=white)](https://discord.gg/jK6F9mmC7f)

[![Support Helix on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Helix-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/unifiedkings)

## Features

- Search and play music from a Subsonic-compatible library
- Play tracks, albums, playlists, and generated stations
- Create stations from artists, artist collections, and tags
- Import playlists from Helix, YouTube Music, Spotify, and Pandora with a review step before anything is added
- Run shared listening lobbies with guest queue and playback controls
- Join lobbies with simple 5-letter codes and optional password protection
- Mirror a Helix lobby into Discord voice with the optional [HelixBot](https://github.com/UnifiedKings/helixbot) companion project
- Use the optional native [Helix for Android](https://github.com/UnifiedKings/helix-android) client for mobile playback and control
- Repair metadata before finalized library imports
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

## Playlist importing

Helix can bring an existing playlist into a Helix playlist without blindly copying whatever metadata the source happens to provide. Imports are previewed first, matched against playable music, and only the tracks you approve are added.

The current import sources are:

- **Helix** — export a playlist from another Helix instance as JSON and upload the file.
- **YouTube Music** — paste a normal playlist Share link. For **Liked Music**, save the Liked Music page from your browser as an HTML file and upload it.
- **Spotify** — export a playlist or Liked Songs with [Exportify](https://exportify.net/) and upload the resulting CSV.
- **Pandora** — open a playlist or **My Thumbs Up**, choose Share, and paste the public Pandora playlist link. Public Pandora playlists are imported using Pandora's anonymous web session flow; a Pandora login is not required.

### Matching and cleanup

For sources that do not already contain a clean playable Helix track reference, Helix tries to resolve each entry to a YouTube Music song before import. This is especially useful for ordinary YouTube-backed playlists, where the original item may be a lyric video, soundtrack upload, fan upload, or another video with messy title/channel metadata.

Helix prefers canonical song metadata where it can find it, including the song title, artist, album information, duration, and proper square artwork. It also tries cleaned title/artist search variants rather than automatically trusting the original YouTube video metadata.

Every previewed track is classified as one of the following:

- **Matched** — Helix found a strong candidate and selects it by default.
- **Review** — Helix found a possible match, but confidence is low enough that it should be checked before import.
- **Unmatched** — no usable candidate was found automatically.
- **Already here** — the destination playlist already contains the track.

The review screen lets you choose exactly which tracks will be imported. You can also filter down to tracks that need attention and leave **Skip songs already in this playlist** enabled to avoid duplicates.

Playlist importing does **not** automatically add every imported song to Subsonic. Imported playlist entries can play from their resolved source, and individual tracks can still be sent to **Add to Subsonic** separately when fulfillment is enabled.

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

      # Fulfillment staging
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

## yt-dlp updates

Helix relies on `yt-dlp` for source playback and fulfillment. Source behavior can change frequently, and an outdated `yt-dlp` can cause those features to fail even when the rest of Helix is working normally.

To reduce that failure mode, the Docker image includes a bundled version of `yt-dlp` **and Helix checks for an updated version each time the container starts**. This happens before the Helix application starts, so users do not need to rebuild or pull a new Helix image solely to receive a newer `yt-dlp`.

The default settings are:

```env
HELIX_YTDLP_AUTO_UPDATE=true
HELIX_YTDLP_CHANNEL=stable
```

`HELIX_YTDLP_CHANNEL` supports:

- `stable` — the latest stable `yt-dlp` release. This is the default.
- `nightly` — allows pre-release/nightly builds when a source fix has not reached stable yet.

Set `HELIX_YTDLP_AUTO_UPDATE=false` if you specifically want to use only the version bundled into the Helix image.

### If the yt-dlp update fails

A failed update **does not prevent Helix from starting**. Helix logs a warning and continues with the version already bundled in the container. This keeps temporary package-index or network outages from taking the entire service down.

However, if that bundled version has become too old for the current source behavior, source playback and fulfillment may fail until `yt-dlp` can be updated. If those features suddenly stop working, check the container startup logs for an entry beginning with `[helix-entrypoint]` before assuming the Helix application itself is broken. Restarting the container when network/package access is available will retry the update; pulling a current Helix image also refreshes the bundled fallback version.

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

HelixBot links a Discord server to a Helix lobby using its 5-letter join code, joins Discord voice, and mirrors the lobby's current playback. The bot follows track changes, pause/resume, seeks, and queue advancement rather than exposing separate Discord playback controls.

HelixBot runs in its own Docker container and can be configured to connect to any reachable Helix instance.

## Helix for Android

[Helix for Android](https://github.com/UnifiedKings/helix-android) is the optional native Android client for Helix.

It connects to an existing Helix server and shares the same playback state and queue as the web frontend. The Android app includes native Media3 playback, lock-screen and notification controls, search, stations, playlists, album browsing, queue management, liked/disliked tracks, and Subsonic availability/import controls.

The app also has its own native appearance settings, independent of the web frontend theme.

Compiled APK releases are available from the [Helix for Android Releases](https://github.com/UnifiedKings/helix-android/releases) page. Android Studio is only required if you want to build or modify the app from source.

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

Helix is licensed under the [GNU Affero General Public License v3.0](LICENSE) (**AGPL-3.0-only**).

You may use, modify, and redistribute Helix under the terms of the AGPL. If you modify Helix and make that modified version available to users over a network, the corresponding source code must also be made available as required by the license.
