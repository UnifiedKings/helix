export function esc(s) { return (s ?? "").toString(); }

export function fmtMs(ms) {
  if (!ms || isNaN(ms)) return "";
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  const r = String(s % 60).padStart(2, "0");
  return `${m}:${r}`;
}

export function norm(s) {
  return (s || "")
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function qPhrase(s) {
  return `"${String(s).replace(/"/g, '\\"')}"`;
}

/**
 * Parse "Artist - Title", "Title by Artist", and lightweight key:value pairs.
 * Supported keys: artist, title, track, recording, album, release, year, date, dur, duration
 */
export function parseSmartQuery(freeText) {
  const raw = (freeText || "").trim();
  const out = { artist: "", title: "", album: "", year: "", durationSec: "" };
  if (!raw) return out;

  const kv = {};
  raw.replace(/(\w+)\s*:\s*(\".*?\"|\S+)/g, (_, k, v) => {
    const key = k.toLowerCase();
    let val = v;
    if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
    kv[key] = val;
    return _;
  });

  if (kv.artist) out.artist = kv.artist;
  if (kv.title) out.title = kv.title;
  if (kv.track) out.title = kv.track;
  if (kv.recording) out.title = kv.recording;

  if (kv.album) out.album = kv.album;
  if (kv.release) out.album = kv.release;

  if (kv.year) out.year = kv.year;
  if (kv.date) out.year = kv.date;

  if (kv.dur) out.durationSec = kv.dur;
  if (kv.duration) out.durationSec = kv.duration;

  if (!out.artist && !out.title) {
    const m = raw.match(/^(.+?)\s*-\s*(.+)$/);
    if (m) {
      out.artist = m[1].trim();
      out.title = m[2].trim();
      return out;
    }
  }

  if (!out.artist && !out.title) {
    const m = raw.match(/^(.+?)\s+by\s+(.+)$/i);
    if (m) {
      out.title = m[1].trim();
      out.artist = m[2].trim();
      return out;
    }
  }

  return out;
}

export function buildAdvancedQuery({ type, free, artist, title, album, year }) {
  if (!artist && !title && !album && !year) return free;

  const parts = [];
  if (artist) parts.push(`artist:${qPhrase(artist)}`);

  if (type === "recording") {
    if (title) parts.push(`recording:${qPhrase(title)}`);
    if (album) parts.push(`release:${qPhrase(album)}`);
    if (year) parts.push(`date:${year}`);
  } else if (type === "release-group") {
    if (title) parts.push(`releasegroup:${qPhrase(title)}`);
    if (year) parts.push(`firstreleasedate:${year}`);
  } else {
    if (title) parts.push(`release:${qPhrase(title)}`);
    if (year) parts.push(`date:${year}`);
  }

  return parts.join(" AND ");
}
