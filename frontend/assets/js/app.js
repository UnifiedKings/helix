import { startPlayerPolling } from "./player.js";
import * as backend from "./api/backend.js";
import { initTopNav } from "./ui/topnav.js";
import { createThrottledFetch } from "./utils/throttle.js";
import { esc, fmtMs } from "./utils/text.js";
import { activateImage, coverUrlForRelease, coverUrlForReleaseGroup, clearCover, setCoverEntity } from "./api/coverart.js";
import {
  lookupRecording,
  lookupReleaseGroup,
} from "./api/musicbrainz.js";

const throttledFetch = createThrottledFetch(1100);

let els = {};

function refreshEls() {
  els = {
    input: document.getElementById("globalSearch"),
    clearBtn: document.getElementById("clearSearch"),
    tabs: Array.from(document.querySelectorAll(".tabBtn")),
    sourceBtns: Array.from(document.querySelectorAll(".sourceBtn")),
    status: document.getElementById("status"),
    results: document.getElementById("results"),
    count: document.getElementById("count"),
    details: document.getElementById("details"),
    links: document.getElementById("links"),
    topSearch: document.querySelector(".topSearch"),
  };
}

function setStatus(t) {
  if (els.status) els.status.textContent = t;
}

function setLoading(isLoading) {
  if (!els.topSearch) return;
  els.topSearch.classList.toggle("loading", !!isLoading);
}

function getUrlState() {
  const p = new URLSearchParams(window.location.search);
  return {
    q: (p.get("q") || "").trim(),
    tab: (p.get("tab") || "all").trim().toLowerCase(),
    // YouTube Music is now the primary source.
    source: (p.get("source") || "ytmusic").trim().toLowerCase(),
  };
}

function replaceUrlState({ q, tab, source }, { push = false } = {}) {
  const url = new URL(window.location.href);
  if (q) url.searchParams.set("q", q);
  else url.searchParams.delete("q");

  if (tab && tab !== "all") url.searchParams.set("tab", tab);
  else url.searchParams.delete("tab");

  const src = (source || "ytmusic").trim().toLowerCase();
  if (src && src !== "ytmusic") url.searchParams.set("source", src);
  else url.searchParams.delete("source");

  const method = push ? "pushState" : "replaceState";
  history[method]({}, "", url.toString());
}

function debounce(fn, waitMs) {
  let t = null;
  const debounced = (...args) => {
    if (t) window.clearTimeout(t);
    t = window.setTimeout(() => {
      t = null;
      fn(...args);
    }, waitMs);
  };
  debounced.cancel = () => {
    if (t) window.clearTimeout(t);
    t = null;
  };
  return debounced;
}

function norm(s) {
  return String(s || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function matchScore(text, q) {
  const a = norm(text);
  const b = norm(q);
  if (!b) return 0;
  if (a === b) return 120;
  if (a.startsWith(b)) return 80;
  if (a.includes(b)) return 45;
  return 0;
}

function svgInitials(label) {
  const safe = String(label || "?").trim();
  const initial = (safe[0] || "?").toUpperCase();
  const svg = `<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">
      <rect width="96" height="96" rx="18" ry="18" fill="#0f1217"/>
      <text x="50%" y="56%" text-anchor="middle" font-family="system-ui, -apple-system, Segoe UI" font-size="44" fill="#e7e7e7">${initial}</text>
    </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function pickRepresentativeRelease(releases, settings) {
  const defCountry = String(settings?.search_default_country || "US").toUpperCase();
  const hideNonOfficial = settings?.search_hide_non_official !== false;
  const preferOriginal = !!settings?.search_prefer_original_release;

  const list = Array.isArray(releases) ? releases.slice() : [];
  if (!list.length) return null;

  const isOfficial = (r) => (String(r.status || "").toLowerCase() === "official");
  const normCountry = (r) => String(r.country || "").toUpperCase();
  const yearOf = (r) => {
    const d = String(r.date || "");
    const y = parseInt(d.slice(0, 4), 10);
    return Number.isFinite(y) ? y : null;
  };

  let candidates = list;
  if (hideNonOfficial) {
    const official = candidates.filter(isOfficial);
    if (official.length) candidates = official;
  }

  const byCountry = candidates.filter((r) => normCountry(r) === defCountry);
  if (byCountry.length) candidates = byCountry;

  // Prefer earliest release if requested, otherwise use earliest as tie-break.
  const withYear = candidates.map((r) => ({ r, y: yearOf(r) })).filter(x => x.y !== null);
  if (withYear.length) {
    withYear.sort((a, b) => a.y - b.y);
    if (preferOriginal) return withYear[0].r;
    // not preferOriginal: still choose earliest among country-matching official
    return withYear[0].r;
  }
  return candidates[0] || null;
}

function canonicalTrackKey(rec) {
  const title = norm(rec.title || "");
  const ac0 = rec["artist-credit"]?.[0];
  const artistId = ac0?.artist?.id || "";
  const artistName = ac0?.name || "";
  return `${title}::${artistId || norm(artistName)}`;
}

function buildRow(item) {
  const div = document.createElement("div");
  div.className = "item";
  div.dataset.id = item.id;
  div.dataset.kind = item.kind;

  const img = document.createElement("img");
  img.className = "thumb";
  img.alt = "";
  img.loading = "lazy";

  const thumbWrap = document.createElement("div");
  thumbWrap.className = "thumbWrap";
  thumbWrap.appendChild(img);

  // Play overlay for songs and albums (including YT Music results)
  const isSong = item.kind === "song" || item.kind === "yt_song";
  const isAlbum = item.kind === "album" || item.kind === "yt_album";
  let playBtn = null;
  if ((isSong || isAlbum) && item.id) {
    playBtn = document.createElement("button");
    playBtn.className = "thumbPlayBtn";
    playBtn.type = "button";
    playBtn.title = "Play";
    playBtn.textContent = "▶";
    playBtn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        if (isSong) {
          await backend.playerPlayTrack({
            // recording_id is optional; for YT Music results it will be null.
            recording_id: item.kind === "song" ? item.id : null,
            yt_video_id: item.kind === "yt_song" ? item.id : null,
            title: item.title || "",
            artist: item.artist || "",
            album: item.album || "",
            duration_ms: item.lengthMs || (item.durationMs || undefined),
            art_url: item.thumbSrc || "",
          });
          // Ensure playback starts immediately.
          try { await backend.playerResume(); } catch {}
          document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
        } else {
          if (item.kind === "yt_album") {
            await backend.playerPlayAlbumYt({ browse_id: item.id, title: item.title || null, artist: item.artist || null, art_url: item.thumbSrc || null });
          } else {
            await backend.playerPlayAlbum(item.id);
          }
          try { await backend.playerResume(); } catch {}
          document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
        }
      } catch (e) {
        console.error(e);
      }
    });
    thumbWrap.appendChild(playBtn);
  }

  // "More" menu (…): Play / Add to Queue
  const more = document.createElement("button");
  more.className = "rowMoreBtn";
  more.type = "button";
  more.title = "More";
  more.textContent = "⋯";

  const menu = document.createElement("div");
  menu.className = "rowMoreMenu";
  menu.innerHTML = `
    <button type="button" data-action="play">Play</button>
    <button type="button" data-action="queue">Add to queue</button>
    <button type="button" data-action="station">Create station</button>
  `;

  function closeMenu() { menu.classList.remove("open"); }
  function toggleMenu() { menu.classList.toggle("open"); }

  more.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    toggleMenu();
  });

  menu.addEventListener("click", async (ev) => {
    const btn = ev.target?.closest?.("button");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const action = btn.dataset.action;
    closeMenu();
    try {
      if (action === "play") {
        // Reuse the thumb play handler by clicking it.
        playBtn?.click?.();
        return;
      }

      if (action === "queue") {
        if (isSong) {
          await backend.playerQueueAppendTrack({
            recording_id: item.kind === "song" ? item.id : null,
            yt_video_id: item.kind === "yt_song" ? item.id : null,
            title: item.title || "",
            artist: item.artist || "",
            album: item.album || "",
            duration_ms: item.lengthMs || (item.durationMs || undefined),
            art_url: item.thumbSrc || "",
          });
        } else {
          // Album queueing
          if (item.kind === "yt_album") {
            await backend.playerQueueAppendAlbumYt({ browse_id: item.id, title: item.title || null, artist: item.artist || null, art_url: item.thumbSrc || null });
          } else {
            // legacy
            await backend.playerQueueAppendAlbumYt({ browse_id: item.id });
          }
        }
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: false } }));
        return;
      }

      if (action === "station") {
        // Create a track-seeded station and auto-play it.
        // Only makes sense for songs (we'll still attempt with whatever metadata we have).
        const tTitle = item.title || "";
        const tArtist = item.artist || "";
        const defaultName = `${tTitle} Radio`.trim() || "New Station";
        const name = prompt("Station name:", defaultName);
        if (!name) return;

        const created = await backend.stationsCreate({
          name: name.trim(),
          seed_type: "track",
          seed_artist: tArtist,
          seed_title: tTitle,
        });

        await playStation(created.id)
        window.location.href = "index.html";
        return;
      }
    } catch (e) {
      console.error(e);
    }
  });

  // Close menu when clicking elsewhere
  document.addEventListener("click", (ev) => {
    if (!div.contains(ev.target)) closeMenu();
  });

  const left = document.createElement("div");
  const primary = document.createElement("div");
  primary.className = "resultPrimary";
  primary.innerHTML = item.primaryHtml;
  const secondary = document.createElement("div");
  secondary.className = "resultSecondary";
  secondary.innerHTML = item.secondaryHtml;
  left.appendChild(primary);
  left.appendChild(secondary);

  const right = document.createElement("div");
  right.className = "resultRight";
  const rightText = document.createElement("div");
  rightText.className = "resultRightText";
  rightText.textContent = item.rightText || "";
  right.appendChild(rightText);

  // Only show the "…" menu for playable entities (songs/albums).
  if (isSong || isAlbum) {
    const moreWrap = document.createElement("div");
    moreWrap.className = "rowMoreWrap";
    moreWrap.appendChild(more);
    moreWrap.appendChild(menu);
    right.appendChild(moreWrap);
  }

  const row = document.createElement("div");
  row.className = "resultRowPandora";
  row.appendChild(thumbWrap);
  row.appendChild(left);
  row.appendChild(right);

  div.appendChild(row);

  if (item.thumbSrc) {
    // Try proxied thumb first; if it fails (proxy blocked/misrouted), fall back to remote URL if provided.
    img.onerror = () => {
      img.onerror = null;
      if (item.thumbRemote) {
        img.src = item.thumbRemote;
        img.classList.add("loaded");
      } else {
        img.style.visibility = "visible";
        img.src = svgInitials(item.fallbackLabel || "?");
        img.classList.add("loaded");
      }
    };
    activateImage(img, item.thumbSrc);
  } else {
    // Fallback thumbnail (e.g., artist initials). Ensure it is visible (opacity=1) even without onload.
    img.style.visibility = "visible";
    img.src = svgInitials(item.fallbackLabel || "?");
    img.classList.add("loaded");
  }

  div.addEventListener("click", () => {
    if (item.kind === "artist" && item.id) {
      const href = `artist.html?id=${encodeURIComponent(item.id)}`;
      if (typeof window.helixNavigate === "function") window.helixNavigate(href);
      else window.location.href = href;
      return;
    }
    if ((item.kind === "album" || item.kind === "yt_album") && item.id) {
      const href = `album.html?id=${encodeURIComponent(item.id)}`;
      if (typeof window.helixNavigate === "function") window.helixNavigate(href);
      else window.location.href = href;
      return;
    }
    showDetails(item);
  });
  return div;
}

function setDetails(rows, links) {
  if (!els.details) return;
  els.details.innerHTML = rows
    .map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`)
    .join("");
  if (els.links) {
    els.links.innerHTML = (links || [])
      .map((l) => `<a href="${l.href}" target="_blank" rel="noopener">${esc(l.text)}</a>`)
      .join("");
  }
}

let SETTINGS = null;
let LAST = { q: "", tab: "all", items: [] };

async function showDetails(item) {
  clearCover();

  // YT MUSIC mode results (no MusicBrainz ids available)
  if (item.kind === "yt_song") {
    const rows = [
      ["Type", "Song"],
      ["Title", esc(item.title || "")],
      ["Artist", esc(item.artist || "")],
      ["Album", esc(item.album || "")],
      ["Duration", esc(item.duration || "")],
    ];
    const links = [];
    if (item.ytmusic_url) links.push({ text: "View on YouTube Music", href: item.ytmusic_url });
    setDetails(rows, links);
    return;
  }

  if (item.kind === "yt_album") {
    const rows = [
      ["Type", "Album"],
      ["Title", esc(item.title || "")],
      ["Artist", esc(item.artist || "")],
      ["Year", esc(item.year || "")],
    ];
    const links = [];
    if (item.ytmusic_url) links.push({ text: "View on YouTube Music", href: item.ytmusic_url });
    setDetails(rows, links);
    return;
  }

  // Lightweight details for list items; deeper lookup only when needed.
  if (item.kind === "artist") {
    setDetails(
      [
        ["Type", "Artist"],
        ["Name", esc(item.title || item.artist || "")],
      ],
      [{ text: "View on MusicBrainz", href: `https://musicbrainz.org/artist/${item.id}` }]
    );
    return;
  }

  if (item.kind === "album") {
    setCoverEntity("release-group", item.id, `${item.title} — ${item.artist}`);
    const rows = [
      ["Type", "Album"],
      ["Title", esc(item.title)],
      ["Artist", esc(item.artist)],
      ["First release", esc(item.firstReleaseDate || "")],
      ["YouTube Music", `<span class="muted">Checking…</span>`],
    ];
    const links = [{ text: "View on MusicBrainz", href: `https://musicbrainz.org/release-group/${item.id}` }];
    setDetails(rows, links);

    // Ask backend (yt-dlp) if we can find this album on YT Music.
    try {
      const hit = await backend.ytmusicFind({ kind: "album", title: item.title, artist: item.artist });
      const found = !!hit?.found;
      rows[rows.length - 1] = [
        "YouTube Music",
        found ? `<span>Found</span>` : `<span class="muted">Not found</span>`,
      ];
      if (found && hit.ytmusic_url) {
        links.push({ text: "View on YouTube Music", href: hit.ytmusic_url });
      }
      setDetails(rows, links);
    } catch (e) {
      rows[rows.length - 1] = ["YouTube Music", `<span class="muted">Lookup failed</span>`];
      setDetails(rows, links);
    }
    return;
  }

  if (item.kind === "song") {
    // We already chose a representative release for display. Use it for cover.
    if (item.repReleaseId) setCoverEntity("release", item.repReleaseId, `${item.title} — ${item.artist}`);
    const rows = [
      ["Type", "Song"],
      ["Title", esc(item.title)],
      ["Artist", esc(item.artist)],
      ["Album", esc(item.album || "")],
      ["Duration", esc(item.duration || "")],
      ["YouTube Music", `<span class="muted">Checking…</span>`],
    ];
    const links = [{ text: "View on MusicBrainz", href: `https://musicbrainz.org/recording/${item.id}` }];
    setDetails(rows, links);

    // Parse mm:ss duration if present.
    let durS = null;
    if (item.duration && typeof item.duration === "string" && item.duration.includes(":")) {
      const parts = item.duration.split(":").map((x) => parseInt(x, 10));
      if (parts.length === 2 && parts.every((n) => Number.isFinite(n))) durS = parts[0] * 60 + parts[1];
      if (parts.length === 3 && parts.every((n) => Number.isFinite(n))) durS = parts[0] * 3600 + parts[1] * 60 + parts[2];
    }

    try {
      const hit = await backend.ytmusicFind({
        kind: "song",
        title: item.title,
        artist: item.artist,
        album: item.album || null,
        duration_seconds: durS,
      });
      const found = !!hit?.found;
      rows[rows.length - 1] = [
        "YouTube Music",
        found ? `<span>Found</span>` : `<span class="muted">Not found</span>`,
      ];
      if (found && hit.ytmusic_url) {
        links.push({ text: "View on YouTube Music", href: hit.ytmusic_url });
      }
      setDetails(rows, links);
    } catch (e) {
      rows[rows.length - 1] = ["YouTube Music", `<span class="muted">Lookup failed</span>`];
      setDetails(rows, links);
    }
    return;
  }
}

function setActiveTab(tab) {
  els.tabs.forEach((b) => {
    const isActive = (b.dataset.tab || "").toLowerCase() === tab;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function setActiveSource(source) {
  const s = (source || "library").toLowerCase();
  (els.sourceBtns || []).forEach((b) => {
    const isActive = (b.dataset.source || "").toLowerCase() === s;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  // In YT MUSIC mode, we only support All/Songs/Albums.
  const showTabs = new Set(s === "ytmusic" ? ["all", "songs", "albums"] : ["all", "artists", "albums", "songs", "stations", "playlists", "podcasts"]);
  (els.tabs || []).forEach((b) => {
    const t = (b.dataset.tab || "").toLowerCase();
    b.style.display = showTabs.has(t) ? "" : "none";
  });
}

function renderItems(items) {
  if (!els.results) return;
  els.results.innerHTML = "";
  if (!items.length) {
    els.results.innerHTML = `<div class="item"><div>No results.</div><div class="muted">Try another search.</div></div>`;
    if (els.count) els.count.textContent = "";
    return;
  }
  items.forEach((it) => els.results.appendChild(buildRow(it)));
  if (els.count) els.count.textContent = `${items.length.toLocaleString()} shown`;
}

async function fetchBackendSearch(q, tab, limit = 25) {
  const data = await backend.search(q, tab, limit, 0);
  return data.results || [];
}

async function runSearch({ q, tab, source }) {
  const src = (source || getUrlState().source || "library").toLowerCase();
  LAST.q = q;
  LAST.tab = tab;
  LAST.source = src;
  clearCover();

  if (!q) {
    setStatus("Type to search");
    renderItems([]);
    setDetails([["Tip", "Start typing in the search box." ]], []);
    return;
  }

  setLoading(true);
  setStatus("Searching…");

  try {
    let items = [];

    {
      const data = await backend.ytmusicSearch(q, 20, 20);
      const songs = Array.isArray(data?.songs) ? data.songs : [];
      const albums = Array.isArray(data?.albums) ? data.albums : [];

      let merged = [];
      if (tab === "songs") merged = songs;
      else if (tab === "albums") merged = albums;
      else merged = [...songs, ...albums];

      // Map into the existing UI item format.
      items = merged.map((it) => {
        if (it.kind === "album") {
          return {
            kind: "yt_album",
            id: it.browse_id,
            title: it.title,
            artist: it.artist,
            year: it.year || "",
            primaryHtml: esc(it.title),
            secondaryHtml: esc([it.artist, it.year].filter(Boolean).join(" • ")),
            rightText: "",
            thumbRemote: it.thumbnail_url || "",
            thumbSrc: it.thumbnail_url || "",
            fallbackLabel: it.title || "Album",
            ytmusic_url: it.ytmusic_url,
          };
        }
        // song
        const dur = it.duration_seconds ? fmtMs(it.duration_seconds * 1000) : "";
        return {
          kind: "yt_song",
          id: it.video_id,
          title: it.title,
          artist: it.artist,
          album: it.album || "",
          duration: dur,
          primaryHtml: esc(it.title),
          secondaryHtml: esc([it.artist, it.album].filter(Boolean).join(" • ")),
          rightText: dur,
          thumbRemote: it.thumbnail_url || "",
          thumbSrc: it.thumbnail_url || "",
          fallbackLabel: it.title || "Song",
          ytmusic_url: it.ytmusic_url,
        };
      });

      setStatus(`${items.length ? "Results" : "No results"}`);
      renderItems(items);
      LAST.items = items;
    }

  } catch (e) {
    setStatus(`Error: ${e.message || e}`);
    renderItems([]);
  } finally {
    setLoading(false);
  }
}

// Search only after the user has paused typing. Enter triggers immediately.
const debouncedSearch = debounce((q, tab) => {
  const source = "ytmusic";
  replaceUrlState({ q, tab, source }, { push: false });
  runSearch({ q, tab, source });
}, 650);

function wireTabs() {
  els.tabs.forEach((b) => {
    b.addEventListener("click", () => {
      const tab = (b.dataset.tab || "all").toLowerCase();
      setActiveTab(tab);
      const q = (els.input?.value || "").trim();
      const source = "ytmusic";
      replaceUrlState({ q, tab, source }, { push: true });
      runSearch({ q, tab, source });
    });
  });
}

function wireSourceTabs() {
  (els.sourceBtns || []).forEach((b) => {
    b.addEventListener("click", () => {
      const source = "ytmusic";
      setActiveSource(source);

      // If current tab is not valid for the source, fall back.
      const cur = getUrlState();
      let tab = (cur.tab || "all").toLowerCase();
      if (source === "ytmusic" && !["all", "songs", "albums"].includes(tab)) tab = "all";

      const q = (els.input?.value || "").trim();
      setActiveTab(tab);
      replaceUrlState({ q, tab, source }, { push: true });
      runSearch({ q, tab, source });
    });
  });
}

function wireInput() {
  if (!els.input) return;

  els.input.addEventListener("input", () => {
    const q = (els.input.value || "").trim();
    const state = getUrlState();
    debouncedSearch(q, state.tab || "all");
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (debouncedSearch.cancel) debouncedSearch.cancel();
      const q = (els.input.value || "").trim();
      const state = getUrlState();
      const tab = state.tab || "all";
      const source = "ytmusic";
      replaceUrlState({ q, tab, source }, { push: true });
      runSearch({ q, tab, source });
    }
  });

  if (els.clearBtn) {
    els.clearBtn.addEventListener("click", () => {
      els.input.value = "";
      const state = getUrlState();
      replaceUrlState({ q: "", tab: state.tab || "all", source: "ytmusic" }, { push: true });
      runSearch({ q: "", tab: state.tab || "all", source: "ytmusic" });
      els.input.focus();
    });
  }
}

function ensureSearchGlobalListenersOnce() {
  if (window.__HELIX_SEARCH_GLOBALS_BOUND) return;
  window.__HELIX_SEARCH_GLOBALS_BOUND = true;

  window.addEventListener("popstate", () => {
    // Only handle popstate when the search UI is present.
    refreshEls();
    if (!els.results) return;
    const { q, tab, source } = getUrlState();
    if (els.input) els.input.value = q;
    setActiveTab(tab || "all");
    setActiveSource(source || "ytmusic");
    runSearch({ q, tab: tab || "all", source: source || "ytmusic" });
  });

  window.addEventListener("helix:globalsearch", (e) => {
    refreshEls();
    if (!els.input) return;
    const q = (e.detail?.q || "").trim();
    els.input.value = q;
    const state = getUrlState();
    const tab = state.tab || "all";
    replaceUrlState({ q, tab, source: "ytmusic" }, { push: true });
    runSearch({ q, tab, source: "ytmusic" });
  });
}

export async function init() {
  refreshEls();
  // If this isn't the search page, do nothing.
  if (!els.results && !document.getElementById("results")) return;

  await initTopNav();
  startPlayerPolling();

  ensureSearchGlobalListenersOnce();

  // Settings are optional; search should still work without them.
  try {
    SETTINGS = await backend.getSettings();
  } catch {
    SETTINGS = null;
  }

  wireTabs();
  wireSourceTabs();
  wireInput();

  const { q, tab, source } = getUrlState();
  setActiveSource(source || "ytmusic");
  if (els.input && q) els.input.value = q;
  setActiveTab(tab || "all");
  runSearch({ q, tab: tab || "all", source: source || "ytmusic" });
}


async function playStation(id)
{
  console.log("Playing station " + id)
  showLoading("Starting station...")
  try{
    await backend.stationsPlay(id, true);
    document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
  }
  finally {
    document.addEventListener("helix-player-state", () => hideLoading());
  }
}