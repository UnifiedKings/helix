import * as backend from "../api/backend.js";
import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";

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

function setStatus(msg) {
  const n = el("plStatus");
  if (!n) return;
  n.textContent = msg || "";
}

function playlistHref(p) {
  // Use special id for liked playlist if system_key=='liked'
  const pid = (p.system_key === "liked") ? "liked" : p.id;
  return `playlist.html?id=${encodeURIComponent(pid)}`;
}

function renderPlaylists(list) {
  const grid = el("playlistsGrid");
  if (!grid) return;
  grid.innerHTML = "";

  if (!list || !list.length) {
    grid.innerHTML = `<div class="muted">No playlists yet.</div>`;
    return;
  }

  for (const p of list) {
    const card = document.createElement("a");
    card.className = "miniCard";
    card.href = playlistHref(p);

    // Use cover as background of an <div> to avoid broken <img> flash.
    const cover = document.createElement("div");
    cover.style.width = "100%";
    cover.style.aspectRatio = "1 / 1";
    cover.style.borderRadius = "14px";
    cover.style.background = "#1a1f28";
    cover.style.backgroundImage = p.thumbnail_url ? `url('${p.thumbnail_url}')` : "";
    cover.style.backgroundSize = "cover";
    cover.style.backgroundPosition = "center";

    const title = document.createElement("div");
    title.className = "t";
    title.textContent = p.name || "";

    const sub = document.createElement("div");
    sub.className = "s";
    sub.textContent = `${Number(p.track_count || 0)} songs`;

    card.appendChild(cover);
    card.appendChild(title);
    card.appendChild(sub);
    grid.appendChild(card);
  }
}

async function loadPlaylists() {
  setStatus("Loading...");
  try {
    const list = await backend.playlistsList();
    renderPlaylists(list);
    setStatus("");
  } catch (e) {
    setStatus(`Failed to load playlists: ${e.message || e}`);
  }
}

function bindCreate() {
  const btn = el("plCreateBtn");
  if (!btn || btn.__helixBound) return;
  btn.__helixBound = true;

  btn.addEventListener("click", async () => {
    const name = prompt("Playlist name?");
    if (!name) return;
    try {
      setStatus("Creating...");
      await backend.playlistsCreate(name.trim());
      await loadPlaylists();
    } catch (e) {
      alert(e.message || e);
      setStatus("");
    }
  });
}

export async function init() {
  await initTopNav();
  startPlayerPolling();
  bindCreate();
  await loadPlaylists();
}
