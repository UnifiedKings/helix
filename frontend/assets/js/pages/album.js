import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";
import { getAlbum, playerPlayAlbumYt, playerPlayTrack, playerQueueAppendTrack, playerResume } from "../api/backend.js";
import { esc, fmtMs } from "../utils/text.js";
import { showLoading, hideLoading } from "../ui/loading.js";

function qs() {
  return new URLSearchParams(window.location.search);
}

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

function setLoading(isLoading) {
  const overlay = document.getElementById("albumLoading");
  const page = document.getElementById("albumPage");
  if (overlay) overlay.style.display = isLoading ? "flex" : "none";
  if (page) page.style.display = isLoading ? "none" : "flex";
}

function setImg(el, src, label) {
  if (!el) return;
  el.onerror = () => {
    el.onerror = null;
    el.src = svgPlaceholder(label);
  };
  el.src = src || svgPlaceholder(label);
}

function renderTracklist(tracks, albumCtx) {
  const root = document.getElementById("albumTracklist");
  if (!root) return;
  root.innerHTML = "";

  const list = tracks || [];
  for (let i = 0; i < list.length; i++) {
    const t = list[i] || {};
    const row = document.createElement("div");
    row.className = "albumTrackRow";
    const idx = t.pos != null ? String(t.pos) : String(i + 1);
    const dur = t.lengthMs != null ? fmtMs(t.lengthMs) : (t.duration_seconds ? fmtMs(t.duration_seconds * 1000) : "");

    // Right-side cluster: duration + ... menu
    const right = document.createElement("div");
    right.className = "trackRight";
    right.innerHTML = `<div class="dur muted">${esc(dur)}</div>`;

    // ... menu (Play / Add to queue)
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
    `;

    const moreWrap = document.createElement("div");
    moreWrap.className = "rowMoreWrap";
    moreWrap.appendChild(more);
    moreWrap.appendChild(menu);

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

      const trackPayload = {
        recording_id: null,
        yt_video_id: t.video_id || null,
        title: t.title || "",
        artist: t.artist || albumCtx.artist || "",
        album: albumCtx.title || "",
        duration_ms: t.lengthMs || (t.duration_seconds ? (t.duration_seconds * 1000) : undefined),
        art_url: albumCtx.art_url || "",
      };

      let __didShowLoading = false;
      try {
        if (action === "play") {
          try { showLoading(`Playing track... ${t.title || "Track"}${t.artist ? " — " + t.artist : ""}`); __didShowLoading = true; } catch {}

          await playerPlayTrack(trackPayload);
          try { await playerResume(); } catch {}
          document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
        } else if (action === "queue") {
          await playerQueueAppendTrack(trackPayload);
          document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: false } }));
        }
      } catch (e) {
        console.error(e);
      } finally {
        if (__didShowLoading) {
          try { hideLoading(); } catch {}
        }
      }
    });

    document.addEventListener("click", (ev) => {
      if (!row.contains(ev.target)) closeMenu();
    });

    right.appendChild(moreWrap);

    row.innerHTML = `
      <div class="idx">${esc(idx)}</div>
      <div class="title">${esc(t.title || "")}</div>
    `;
    row.appendChild(right);
    root.appendChild(row);
  }
}

function renderLinks(data) {
  const root = document.getElementById("albumLinks");
  if (!root) return;
  root.innerHTML = "";

  const links = [];
  if (data.ytmusic_url) links.push({ text: "View on YouTube Music", href: data.ytmusic_url });

  for (const l of links) {
    const a = document.createElement("a");
    a.href = l.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = l.text;
    root.appendChild(a);
  }
}

export async function init() {
  await initTopNav();
  startPlayerPolling();
  const id = (qs().get("id") || "").trim();
  if (!id) {
    setLoading(false);
    document.getElementById("albumTitle").textContent = "Album";
    document.getElementById("albumSub").textContent = "Missing id";
    return;
  }

  setLoading(true);
  let data;
  try {
    data = await getAlbum(id);
  } catch (e) {
    setLoading(false);
    document.getElementById("albumTitle").textContent = "Album";
    document.getElementById("albumSub").textContent = String(e?.message || e);
    return;
  }

  const playBtn = document.getElementById("albumPlayBtn");
  if (playBtn) {
    playBtn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try {
        try { showLoading(`Playing album... ${data.title || "Album"}${data.artist ? " — " + data.artist : ""}`); } catch {}
        await playerPlayAlbumYt({ browse_id: id, title: data.title || null, artist: data.artist || null, art_url: data.coverSrc || data.thumbnail_url || null });
        // Ensure playback starts immediately.
        try { await playerResume(); } catch {}
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch (e) {
        console.error(e);
      } finally {
        try { hideLoading(); } catch {}
      }
    });
  }

  const title = data.title || "Album";
  const artist = data.artist || "";
  document.title = `Helix — ${title}`;
  document.getElementById("albumTitle").textContent = title;
  document.getElementById("albumSub").textContent = artist ? `Album by ${artist}` : "Album";

  const trackCount = (data.tracks || []).length || data.trackCount || 0;
  const year = data.year ? String(data.year) : "";
  document.getElementById("albumMeta").textContent = `${trackCount} songs${year ? ` • ${year}` : ""}`;

  const art = data.coverSrc || data.thumbnail_url || data.coverRemote || "";
  setImg(document.getElementById("albumCover"), art, title);
  renderTracklist(data.tracks || [], { title, artist, art_url: art });
  renderLinks(data);

  setLoading(false);
}
