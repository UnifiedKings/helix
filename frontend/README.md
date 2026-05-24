# Helix React Frontend

Fresh React + Vite + TypeScript browser frontend for the cleaned Helix backend API.

## Start dev server

```bash
npm install
npm run dev
```

Vite proxies API requests to `http://localhost:10011` in `vite.config.ts`.

## Current scope

This first pass includes:

- Search page using `GET /api/ytmusic/search`
- Playback controls using `/api/playback/*`
- Queue panel using `/api/queue/*`
- Stations page using `/api/stations/*`
- Playlists page using `/api/playlists/*`
- Settings/status page using `/health` and `/settings`
- Login page wired to `/auth/login`

The frontend intentionally does not call YT Music, yt-dlp, or Subsonic directly. It only speaks to Helix.

## Suggested next work

- Add auth guard/redirect behavior once setup/login expectations are finalized.
- Add artist pages and album detail pages.
- Add playlist track editing UI.
- Replace player polling with backend websocket events when the event contract is stable.
- Generate TypeScript API types from FastAPI OpenAPI once the backend schema settles.
