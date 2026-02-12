import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";
import { getArtist } from "../api/backend.js";
import { esc, fmtMs } from "../utils/text.js";

function svgPlaceholder(label = "?") {
  const safe = String(label || "?").trim();
  const initial = (safe[0] || "?").toUpperCase();
  const svg = `<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="600" height="600">
      <rect width="100%" height="100%" fill="#0b0f14"/>
      <rect x="16" y="16" width="568" height="568" rx="24" fill="#0f1217" stroke="#232833"/>
      <text x="50%" y="54%" text-anchor="middle" font-family="system-ui, -apple-system, Segoe UI" font-size="96" fill="#e7e7e7">${initial}</text>
    </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function qs() {
  return new URLSearchParams(window.location.search);
}

function setImg(el, src, fallbackLabel = "?") {
  if (!el) return;
  el.onerror = () => {
    el.onerror = null;
    el.src = svgPlaceholder(fallbackLabel);
  };
  el.src = src || svgPlaceholder(fallbackLabel);
}

function renderTracks(tracks) {
  const root = document.getElementById("artistTopSongs");
  if (!root) return;
  root.innerHTML = "";

  for (let i = 0; i < (tracks || []).length; i++) {
    const t = tracks[i];
    const row = document.createElement("div");
    row.className = "artistTrackRow";
    const dur = (t.lengthMs != null) ? fmtMs(t.lengthMs) : "";
    row.innerHTML = `
      <div class="idx">${i + 1}</div>
      <div class="title">${esc(t.title || "")}</div>
      <div class="dur muted">${esc(dur)}</div>
      <div class="actions"><button class="iconBtn" title="Play">▶</button></div>
    `;
    root.appendChild(row);
  }
}

function renderAlbums(albums) {
  const root = document.getElementById("artistTopAlbums");
  if (!root) return;
  root.innerHTML = "";

  for (const a of (albums || [])) {
    const card = document.createElement("a");
    // Open the album page for this release-group.
    card.href = `album.html?id=${encodeURIComponent(a.id || "")}`;
    card.className = "albumCard";
    card.innerHTML = `
      <div class="albumArtWrap"><img class="albumArt" alt="" loading="lazy"></div>
      <div class="albumTitle">${esc(a.title || "")}</div>
      <div class="albumMeta muted">${esc(a.year ? String(a.year) : "")}</div>
    `;
    const img = card.querySelector("img");
    img.onerror = () => {
      img.onerror = null;
      img.src = svgPlaceholder(a.title || "?");
    };
    img.src = a.thumbSrc || a.coverSrc || a.thumbRemote || a.coverRemote || svgPlaceholder(a.title || "?");
    root.appendChild(card);
  }
}

export async function init() {
  await initTopNav();
  startPlayerPolling();
const id = (qs().get("id") || "").trim();
  if (!id) {
    document.getElementById("artistName").textContent = "Artist";
    document.getElementById("artistMeta").textContent = "Missing id";
    return;
  }

  let data;
  try {
    data = await getArtist(id);
  } catch (e) {
    document.getElementById("artistName").textContent = "Artist";
    document.getElementById("artistMeta").textContent = String(e?.message || e);
    return;
  }

  const name = data.name || "Artist";
  document.getElementById("artistName").textContent = name;
  document.title = `Helix — ${name}`;
  document.getElementById("artistMeta").textContent = data.type ? String(data.type) : "";

  const portrait = document.getElementById("artistPortrait");
  setImg(portrait, data.imageSrc || data.imageRemote, name);

  // Hero background uses the portrait if available, otherwise fall back to first album art.
  const bg = document.getElementById("artistHeroBg");
  const bgUrl = data.imageRemote || data.imageSrc || (data.topAlbums?.[0]?.thumbRemote || data.topAlbums?.[0]?.thumbSrc) || "";
  if (bg) {
    if (bgUrl) bg.style.backgroundImage = `url('${bgUrl.replace(/'/g, "\\'")}')`;
    else bg.style.backgroundImage = "none";
  }

  renderTracks(data.topTracks || []);
  renderAlbums(data.topAlbums || []);

  const playBtn = document.getElementById("artistPlayBtn");
  if (playBtn) {
    playBtn.onclick = () => {
      // Frontend-only placeholder: no playback wiring yet.
      const first = (data.topTracks || [])[0];
      if (!first) return;
      playBtn.textContent = `▶ ${first.title}`;
    };
  }
}
