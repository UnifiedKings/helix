import * as backend from "../api/backend.js";
import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";
import { showLoading, hideLoading } from "../ui/loading.js";

let _playlistKeys = new Set();

function el(id) { return document.getElementById(id); }

function esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}


function fmtDur(ms) {
  const s = Math.floor((Number(ms) || 0) / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function qsParam(name) {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get(name);
  } catch {
    return null;
  }
}

function trackPayloadFromSearchItem(it) {
  // search results vary by tab; Helix search returns a unified structure where songs are
  // already in the playerPlayTrack payload shape for most pages.
  return {
    title: it.title || "",
    artist: it.artist || "",
    album: it.album || "",
    duration_ms: it.duration_ms || it.durationMs || it.lengthMs || ((it.duration_seconds || it.durationSeconds) ? (Number(it.duration_seconds || it.durationSeconds) * 1000) : 0),
    art_url: it.art_url || it.thumbnail_url || it.thumbnailUrl || it.thumbnail || "",
    source: it.source || it.kind || "ytmusic",
    subsonic_song_id: it.subsonic_song_id || it.subsonicSongId || "",
    yt_video_id: it.yt_video_id || it.video_id || it.videoId || "",
    yt_browse_id: it.yt_browse_id || it.browse_id || it.browseId || "",
    mb_recording_id: it.mb_recording_id || "",
    mb_artist_id: it.mb_artist_id || "",
  };
}

function renderTracks(playlistId, tracks) {
  const wrap = el("plTracks");
  if (!wrap) return;
  wrap.innerHTML = "";

  if (!tracks || !tracks.length) {
    wrap.innerHTML = `<div class="muted">No tracks yet. Use search above to add some.</div>`;
    return;
  }

  for (const t of tracks) {
    const row = document.createElement("div");
    row.className = "row";

    const art = document.createElement("div");
    art.className = "art";
    art.style.width = "42px";
    art.style.height = "42px";
    art.style.borderRadius = "10px";
    art.style.background = "#1a1f28";
    const au = (t.art_url || "").trim();
    if (au) {
      art.style.backgroundImage = `url('${au}')`;
      art.style.backgroundSize = "cover";
      art.style.backgroundPosition = "center";
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.style.flex = "1";
    meta.style.minWidth = "0";
    meta.innerHTML = `<div class="title">${esc(t.title || "")}</div><div class="muted">${esc(t.artist || "")}${t.album ? ` • ${esc(t.album)}` : ""}</div>`;

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "10px";

    const del = document.createElement("button");
    del.className = "btn";
    del.textContent = "Remove";
    del.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!confirm("Remove this track from the playlist?")) return;
      try {
        await backend.playlistsRemoveTrack(playlistId, t.id);
        await load(playlistId);
      } catch (e) {
        alert(e.message || e);
      }
    });

    right.appendChild(del);

    row.appendChild(art);
    row.appendChild(meta);
    row.appendChild(right);

    wrap.appendChild(row);
  }
}

function renderSearchResults(playlistId, results) {
  const wrap = el("plSearchResults");
  if (!wrap) return;
  wrap.innerHTML = "";

  const songs = (results && results.songs) ? results.songs : [];
  if (!songs || !songs.length) {
    wrap.innerHTML = `<div class="muted">No results.</div>`;
    return;
  }

  const box = document.createElement("div");
  box.className = "list";

  for (const it of songs.slice(0, 25)) {
    const row = document.createElement("div");
    row.className = "row";

    const au = (it.art_url || it.thumbnail_url || "").trim();
    const art = document.createElement("div");
    art.style.width = "42px";
    art.style.height = "42px";
    art.style.borderRadius = "10px";
    art.style.background = "#1a1f28";
    if (au) {
      art.style.backgroundImage = `url('${au}')`;
      art.style.backgroundSize = "cover";
      art.style.backgroundPosition = "center";
    }

    const meta = document.createElement("div");
    meta.style.flex = "1";
    meta.style.minWidth = "0";
    meta.innerHTML = `<div class="title">${esc(it.title || "")}</div><div class="muted">${esc(it.artist || "")}${it.album ? ` • ${esc(it.album)}` : ""}</div>`;

    const key = (it.video_id || it.videoId) ? ("yt:" + (it.video_id || it.videoId)) : ((it.subsonic_song_id || it.subsonicSongId) ? ("subsonic:" + (it.subsonic_song_id || it.subsonicSongId)) : "");
    const already = key && _playlistKeys.has(key);

    const add = document.createElement("button");
    add.className = already ? "btn" : "primaryBtn";
    add.textContent = already ? "Added" : "Add";
    add.disabled = !!already;
    add.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        await backend.playlistsAddTrack(playlistId, trackPayloadFromSearchItem(it));
        // Clear results after adding one; feels snappy.
        const q = el("plAddSearch");
        if (q) q.value = "";
        const r = el("plSearchResults");
        if (r) r.innerHTML = "";
        await load(playlistId);
      } catch (e) {
        alert(e.message || e);
      }
    });

    const dur = document.createElement("div");
    dur.className = "muted";
    const dms = trackPayloadFromSearchItem(it).duration_ms;
    dur.textContent = dms ? fmtDur(dms) : "";
    dur.style.width = "52px";
    dur.style.textAlign = "right";

    row.appendChild(art);
    row.appendChild(meta);
    row.appendChild(dur);
    row.appendChild(add);
    box.appendChild(row);
  }

  wrap.appendChild(box);
}

async function playPlaylist(tracks, { shuffle = false } = {}) {
  if (!tracks || !tracks.length) throw new Error("Playlist is empty");
  const items = tracks.slice();
  if (shuffle) items.sort(() => Math.random() - 0.5);

  // Use playerPlayTrack for first, then append rest.
  const first = items[0];
  await backend.playerPlayTrack({
    title: first.title,
    artist: first.artist,
    album: first.album,
    duration_ms: first.duration_ms,
    art_url: first.art_url,
    yt_video_id: first.yt_video_id,
  });

  for (const t of items.slice(1, 250)) {
    await backend.playerQueueAppendTrack({
      title: t.title,
      artist: t.artist,
      album: t.album,
      duration_ms: t.duration_ms,
      art_url: t.art_url,
      yt_video_id: t.yt_video_id,
    });
  }

  document.dispatchEvent(new CustomEvent("helix-player-refresh", {
    detail: { forceLoadStream: true }
  }));
}

async function load(playlistId) {
  const status = el("plDetailStatus");
  if (status) status.textContent = "Loading...";

  const data = await backend.playlistsGet(playlistId);
  const p = data.playlist;
  const tracks = data.tracks || [];

  _playlistKeys = new Set();
  for (const t of tracks) {
    const sid = (t.subsonic_song_id || "").trim();
    const vid = (t.yt_video_id || "").trim();
    if (sid) _playlistKeys.add("subsonic:" + sid);
    if (vid) _playlistKeys.add("yt:" + vid);
  }

  const cover = el("plCover");
  if (cover) {
    const url = p.thumbnail_url || "";
    cover.style.backgroundImage = url ? `url('${url}')` : "";
  }

  const title = el("plTitle");
  if (title) title.textContent = p.name || "Playlist";

  const owner = el("plOwner");
  if (owner) owner.textContent = (p.system_key === "liked") ? "Smart Playlist" : "Playlist";

  const count = el("plCount");
  if (count) count.textContent = `${tracks.length} songs`;

  const hint = el("plTracksHint");
  if (hint) hint.textContent = (p.system_key === "liked") ? "Tracks appear here when you like them." : "";

  renderTracks(playlistId, tracks);

  if (status) status.textContent = "";

  // Bind play buttons
  const playBtn = el("plPlayBtn");
  if (playBtn && !playBtn.__bound) {
    playBtn.__bound = true;
    playBtn.addEventListener("click", async () => {
      let __shown = false;
      try {
        const d = await backend.playlistsGet(playlistId);
        try { showLoading(`Playing playlist... ${d.playlist?.name || ""}`.trim()); __shown = true; } catch {}
        await playPlaylist(d.tracks || [], { shuffle: false });
      } catch (e) {
        alert(e.message || e);
      } finally {
        if (__shown) { try { hideLoading(); } catch {} }
      }
    });
  }

  const shufBtn = el("plShuffleBtn");
  if (shufBtn && !shufBtn.__bound) {
    shufBtn.__bound = true;
    shufBtn.addEventListener("click", async () => {
      let __shown = false;
      try {
        const d = await backend.playlistsGet(playlistId);
        try { showLoading(`Playing playlist... ${d.playlist?.name || ""}`.trim()); __shown = true; } catch {}
        await playPlaylist(d.tracks || [], { shuffle: true });
      } catch (e) {
        alert(e.message || e);
      } finally {
        if (__shown) { try { hideLoading(); } catch {} }
      }
    });
  }

  const backBtn = el("plBackBtn");
  if (backBtn && !backBtn.__bound) {
    backBtn.__bound = true;
    backBtn.addEventListener("click", () => {
      if (typeof window.helixNavigate === "function") window.helixNavigate("my-collection.html");
      else window.location.href = "my-collection.html";
    });
  }
}

function bindSearch(playlistId) {
  const inp = el("plAddSearch");
  const clear = el("plAddClear");
  if (clear && !clear.__bound) {
    clear.__bound = true;
    clear.addEventListener("click", () => {
      if (inp) inp.value = "";
      const wrap = el("plSearchResults");
      if (wrap) wrap.innerHTML = "";
    });
  }

  if (!inp || inp.__bound) return;
  inp.__bound = true;

  let last = 0;
  inp.addEventListener("input", async () => {
    const q = (inp.value || "").trim();
    const now = Date.now();
    last = now;
    if (!q) {
      const wrap = el("plSearchResults");
      if (wrap) wrap.innerHTML = "";
      return;
    }
    // debounce ~250ms
    await new Promise((r) => setTimeout(r, 250));
    if (last !== now) return;

    try {
      const res = await backend.ytmusicSearch(q, 25, 0);
      renderSearchResults(playlistId, res);
    } catch (e) {
      const wrap = el("plSearchResults");
      if (wrap) wrap.innerHTML = `<div class="muted">Search failed: ${esc(e.message || e)}</div>`;
    }
  });
}

export async function init() {
  await initTopNav();
  startPlayerPolling();

  const playlistId = qsParam("id") || "";
  if (!playlistId) {
    const st = el("plDetailStatus");
    if (st) st.textContent = "Missing playlist id.";
    return;
  }

  try {
    await load(playlistId);
    bindSearch(playlistId);
  } catch (e) {
    const st = el("plDetailStatus");
    if (st) st.textContent = e.message || String(e);
    alert(e.message || e);
  }
}
