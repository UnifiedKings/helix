const API_BASE = (window.MR_API_BASE || window.MR_CONFIG?.API_BASE || "").replace(/\/$/, "");

async function api(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });

  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) data = await res.json().catch(() => null);
  else data = await res.text().catch(() => null);

  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export async function setupEnabled() { return api("/setup/enabled", { method: "GET" }); }
export async function setup(username, password) { return api("/setup", { method: "POST", body: JSON.stringify({ username, password }) }); }

export async function login(username, password) { return api("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); }
export async function logout() { return api("/auth/logout", { method: "POST", body: JSON.stringify({}) }); }
export async function me() { return api("/auth/me", { method: "GET" }); }

// Admin
export async function adminGetUsers() { return api("/admin/users", { method: "GET" }); }
export async function adminCreateUser(username, password, role) {
  return api("/admin/users", { method: "POST", body: JSON.stringify({ username, password, role }) });
}
export async function adminUpdateUser(user_id, patch) {
  return api(`/admin/users/${user_id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function adminGetSettings() { return api("/admin/settings", { method: "GET" }); }
export async function adminUpdateSettings(patch) {
  // PATCH with a flat object: { key: value, ... }
  return api("/admin/settings", { method: "PATCH", body: JSON.stringify(patch) });
}

// Read-only settings for normal UI
export async function getSettings() { return api("/settings", { method: "GET" }); }

// Search (unified, Pandora-like)
export async function search(q, tab = "all", limit = 25, offset = 0) {
  const u = new URL(API_BASE + "/api/search", window.location.origin);
  if (q) u.searchParams.set("q", q);
  if (tab) u.searchParams.set("tab", tab);
  u.searchParams.set("limit", String(limit));
  u.searchParams.set("offset", String(offset));
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

export async function suggest(q, limit = 8) {
  const u = new URL(API_BASE + "/api/search/suggest", window.location.origin);
  if (q) u.searchParams.set("q", q);
  u.searchParams.set("limit", String(limit));
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

// YouTube Music lookup (via yt-dlp on backend)
export async function ytmusicFind({ kind, title, artist, album = null, duration_seconds = null }) {
  const u = new URL(API_BASE + "/api/ytmusic/find", window.location.origin);
  u.searchParams.set("kind", kind);
  u.searchParams.set("title", title);
  u.searchParams.set("artist", artist);
  if (album) u.searchParams.set("album", album);
  if (duration_seconds != null) u.searchParams.set("duration_seconds", String(duration_seconds));
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

// YouTube Music search (songs + albums only)
export async function ytmusicSearch(q, song_limit = 15, album_limit = 15) {
  const u = new URL(API_BASE + "/api/ytmusic/search", window.location.origin);
  if (q) u.searchParams.set("q", q);
  u.searchParams.set("song_limit", String(song_limit));
  u.searchParams.set("album_limit", String(album_limit));
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

// Artist page (Pandora-like)
export async function getArtist(artistId) {
  return api(`/api/artist/${encodeURIComponent(artistId)}`, { method: "GET" });
}

// Album page (Pandora-like)
export async function getAlbum(releaseGroupId) {
  return api(`/api/album/${encodeURIComponent(releaseGroupId)}`, { method: "GET" });
}


// --- Player / Queue (backend-owned) ---
export async function playerState() {
  return api("/api/player/state");
}

export async function playerPlayTrack(track) {
  return api("/api/player/play/track", {
    method: "POST",
    body: JSON.stringify(track),
  });
}

export async function playerPlayAlbum(releaseGroupId) {
  return api("/api/player/play/album", {
    method: "POST",
    body: JSON.stringify({ browse_id: releaseGroupId }),
  });
}

export async function playerPlayAlbumYt({ browse_id, title = null, artist = null, art_url = null }) {
  return api("/api/player/play/album", {
    method: "POST",
    body: JSON.stringify({ browse_id, title, artist, art_url }),
  });
}

export async function playerQueueAppendTrack(track) {
  return api("/api/player/queue/append/track", {
    method: "POST",
    body: JSON.stringify(track),
  });
}

export async function playerQueueAppendAlbumYt({ browse_id, title = null, artist = null, art_url = null }) {
  return api("/api/player/queue/append/album", {
    method: "POST",
    body: JSON.stringify({ browse_id, title, artist, art_url }),
  });
}

export async function playerNext() {
  return api("/api/player/next", { method: "POST" });
}
export async function playerPrev() {
  return api("/api/player/prev", { method: "POST" });
}

// Called when the audio element reaches the end of the track naturally.
// This lets the backend record a "completed" listen-history entry and advance.
export async function playerEnded(position_ms = null) {
  return api("/api/player/ended", {
    method: "POST",
    body: JSON.stringify({ position_ms }),
  });
}
export async function playerPause() {
  return api("/api/player/pause", { method: "POST" });
}
export async function playerResume() {
  return api("/api/player/resume", { method: "POST" });
}

export async function playerSetAutoplay(enabled) {
  return api("/api/player/autoplay", {
    method: "POST",
    body: JSON.stringify({ enabled: !!enabled }),
  });
}

export async function playerJump(index) {
  return api("/api/player/jump", {
    method: "POST",
    body: JSON.stringify({ index }),
  });
}

export function playerStreamUrl(queueItemId) {
  if (!queueItemId) return API_BASE + "/api/player/stream/current";
  return API_BASE + `/api/player/stream/${encodeURIComponent(queueItemId)}`;
}


export async function getListeningHistory() {
  return await api('/api/player/history');
}


// --- Likes ---
export async function likesList() {
  return api("/api/likes", { method: "GET" });
}

export async function likesIsLiked({ yt_video_id = null, subsonic_song_id = null } = {}) {
  const u = new URL(API_BASE + "/api/likes/is-liked", window.location.origin);
  if (yt_video_id) u.searchParams.set("yt_video_id", yt_video_id);
  if (subsonic_song_id) u.searchParams.set("subsonic_song_id", subsonic_song_id);
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

export async function likesToggle(payload) {
  return api("/api/likes/toggle", { method: "POST", body: JSON.stringify(payload) });
}

// --- Dislikes ---
export async function dislikesIsDisliked({ yt_video_id = null, subsonic_song_id = null } = {}) {
  const u = new URL(API_BASE + "/api/dislikes/is-disliked", window.location.origin);
  if (yt_video_id) u.searchParams.set("yt_video_id", yt_video_id);
  if (subsonic_song_id) u.searchParams.set("subsonic_song_id", subsonic_song_id);
  return api(u.pathname + "?" + u.searchParams.toString(), { method: "GET" });
}

export async function dislikesToggle(payload) {
  return api("/api/dislikes/toggle", { method: "POST", body: JSON.stringify(payload) });
}

// --- Stations ---
export async function stationsList() {
  return api("/api/stations", { method: "GET" });
}

export async function stationsCreate(payload) {
  return api("/api/stations", { method: "POST", body: JSON.stringify(payload) });
}

export async function stationsPlay(station_id, reset = true) {
  return api(`/api/stations/${encodeURIComponent(station_id)}/play`, {
    method: "POST",
    body: JSON.stringify({ reset: !!reset }),
  });
}

export async function stationsDelete(station_id) {
  return api(`/api/stations/${encodeURIComponent(station_id)}`, { method: "DELETE" });
}

export async function stationsUpdate(station_id, payload) {
  return api(`/api/stations/${encodeURIComponent(station_id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload || {}),
  });
}
