const MB_BASE = "https://musicbrainz.org/ws/2";

/** Build a WS/2 URL with fmt=json */
function mbUrl(path, params) {
  const u = new URL(MB_BASE + path);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  u.searchParams.set("fmt", "json");
  return u.toString();
}

export async function searchRecordings(throttledFetch, { query, limit, offset }) {
  // inc=releases so recording results include releases[] for cover thumbnails
  const url = mbUrl("/recording", { query, limit: String(limit), offset: String(offset), inc: "releases" });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function searchArtists(throttledFetch, { query, limit, offset }) {
  const url = mbUrl("/artist", { query, limit: String(limit), offset: String(offset) });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function searchReleases(throttledFetch, { query, limit, offset }) {
  const url = mbUrl("/release", { query, limit: String(limit), offset: String(offset) });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function lookupRecording(throttledFetch, id) {
  const url = mbUrl(`/recording/${id}`, { inc: "artists+releases" });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function lookupRelease(throttledFetch, id) {
  const url = mbUrl(`/release/${id}`, { inc: "recordings+artists" });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}


export async function searchReleaseGroups(throttledFetch, { query, limit, offset }) {
  const url = mbUrl("/release-group", { query, limit: String(limit), offset: String(offset) });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function lookupReleaseGroup(throttledFetch, id) {
  // inc=releases so we can pick a representative release and show "other versions"
  const url = mbUrl(`/release-group/${id}`, { inc: "artists+releases" });
  const res = await throttledFetch(url, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
