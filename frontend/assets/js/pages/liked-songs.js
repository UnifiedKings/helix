import * as backend from "../api/backend.js";
import { startPlayerPolling } from "../player.js";
import { requireAuth } from "../auth-guard.js";

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

async function render() {
  const wrap = el("likedList");
  if (!wrap) return;
  wrap.innerHTML = "";

  let data;
  try {
    data = await backend.likesList();
  } catch (e) {
    wrap.innerHTML = `<div class="muted">Failed to load liked songs: ${esc(e.message || "")}</div>`;
    return;
  }

  const items = (data && data.items) ? data.items : [];
  const cnt = el("likedCount");
  if (cnt) cnt.textContent = `${items.length} song${items.length === 1 ? "" : "s"}`;

  if (!items.length) {
    wrap.innerHTML = `<div class="muted">No liked songs yet. Hit the ♡ button while a track is playing.</div>`;
    return;
  }

  for (const t of items) {
    const row = document.createElement("div");
    row.className = "resultCard";
    row.innerHTML = `
      <div class="resultLeft">
        <img class="resultThumb" alt="" loading="lazy" src="${esc(t.art_url || "")}" />
        <div class="resultText">
          <div class="resultTitle">${esc(t.title || "")}</div>
          <div class="resultSub muted">${esc(t.artist || "")}${t.album ? ` — ${esc(t.album)}` : ""}</div>
        </div>
      </div>
      <div class="resultActions">
        <button class="primaryBtn" type="button" data-act="play">Play</button>
        <button class="iconBtn" type="button" title="Unlike" data-act="unlike">♥</button>
      </div>
    `;

    row.querySelector('[data-act="play"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        await backend.playerPlayTrack({
          title: t.title,
          artist: t.artist,
          album: t.album || "",
          duration_ms: t.duration_ms || 0,
          art_url: t.art_url || "",
          yt_video_id: t.yt_video_id || null,
        });
      } catch {}
    });

    row.querySelector('[data-act="unlike"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        await backend.likesToggle({
          title: t.title,
          artist: t.artist,
          album: t.album || "",
          duration_ms: t.duration_ms || 0,
          art_url: t.art_url || "",
          source: t.source || "",
          subsonic_song_id: t.subsonic_song_id || null,
          yt_video_id: t.yt_video_id || null,
        });
        await render();
      } catch {}
    });

    wrap.appendChild(row);
  }
}

export async function init() {
  await requireAuth();
  startPlayerPolling();
  await render();
}
