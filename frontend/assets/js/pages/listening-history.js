import * as backend from "../api/backend.js";
import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderItem(it) {
  const t = it.track || it;
  const title = escapeHtml(t.title);
  const artist = escapeHtml(t.artist);
  const album = escapeHtml(t.album);
  const art = t.art_url ? `<img class="rowThumb" src="${t.art_url}" alt="">` : `<div class="rowThumb ph"></div>`;
  const tag = it.event ? `<span class="pill">${escapeHtml(it.event)}</span>` : "";
  return `
    <div class="row historyRow" role="button" tabindex="0" data-history-id="${escapeHtml(it.id)}">
      ${art}
      <div class="rowMeta">
        <div class="rowTitle">${title} ${tag}</div>
        <div class="rowSub">${artist}${album ? " • " + album : ""}</div>
      </div>
    </div>
  `;
}

function renderDetail(it) {
  const t = it.track || it;
  const title = escapeHtml(t.title);
  const artist = escapeHtml(t.artist);
  const album = escapeHtml(t.album);
  const art = t.art_url ? `<img class="detailThumb" src="${t.art_url}" alt="">` : `<div class="detailThumb" style="background: rgba(255,255,255,0.06);"></div>`;
  const event = it.event ? escapeHtml(it.event) : "";
  const eventLabel = event ? `<span class="pill">${event}</span>` : "";
  return `
    ${art}
    <div class="detailTitle">${title}</div>
    <div class="detailSub muted">${artist}${album ? " • " + album : ""}</div>
    <div class="detailMeta">${eventLabel}</div>
  `;
}

export async function init() {
  await initTopNav();
  startPlayerPolling()
  const listEl = document.getElementById("historyList");
  const emptyEl = document.getElementById("historyEmpty");
  const detailEl = document.getElementById("historyDetail");
  if (!listEl) return;

  listEl.innerHTML = `<div class="muted" style="padding:12px 0;">Loading…</div>`;

  try {
    const data = await backend.getListeningHistory();
    const items = data?.items || data?.history || [];
    if (!items.length) {
      listEl.innerHTML = "";
      if (emptyEl) emptyEl.style.display = "block";
      if (detailEl) detailEl.innerHTML = `<div class="muted" style="padding: 8px 0;">No listening history yet.</div>`;
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";

    listEl.innerHTML = items.map(renderItem).join("");

    // Select first item by default.
    let selectedId = items[0]?.id;
    if (detailEl) detailEl.innerHTML = renderDetail(items[0]);

    function applySelection() {
      const rows = listEl.querySelectorAll(".historyRow");
      rows.forEach((r) => {
        const rid = r.getAttribute("data-history-id");
        r.classList.toggle("isSelected", rid === String(selectedId));
      });
    }
    applySelection();

    function selectById(id) {
      selectedId = id;
      const found = items.find((x) => String(x.id) === String(id)) || items[0];
      if (detailEl) detailEl.innerHTML = renderDetail(found);
      applySelection();
    }

    listEl.addEventListener("click", (ev) => {
      const row = ev.target.closest?.(".historyRow");
      if (!row) return;
      const id = row.getAttribute("data-history-id");
      if (!id) return;
      selectById(id);
    });

    // Keyboard support (Enter/Space).
    listEl.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const row = ev.target.closest?.(".historyRow");
      if (!row) return;
      const id = row.getAttribute("data-history-id");
      if (!id) return;
      ev.preventDefault();
      selectById(id);
    });
  } catch (e) {
    console.error("Failed to load listening history", e);
    listEl.innerHTML = `<div class="muted" style="padding:12px 0;">Failed to load history.</div>`;
    if (detailEl) detailEl.innerHTML = `<div class="muted" style="padding: 8px 0;">Failed to load history.</div>`;
  }
}
