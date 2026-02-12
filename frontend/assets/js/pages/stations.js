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

async function loadStations() {
  const wrap = el("stationsList");
  if (!wrap) return;
  wrap.innerHTML = "";
  let stations = [];
  try {
    stations = await backend.stationsList();
  } catch (e) {
    wrap.innerHTML = `<div class="muted">Failed to load stations: ${esc(e.message || "")}</div>`;
    return;
  }

  if (!stations.length) {
    wrap.innerHTML = `<div class="muted">No stations yet. Create one above.</div>`;
    return;
  }

  for (const s of stations) {
    const row = document.createElement("div");
    row.className = "resultCard";
    row.innerHTML = `
      <div class="resultLeft">
        <div class="resultText">
          <div class="resultTitle">${esc(s.name || "")}</div>
          <div class="resultSub muted">Seed: ${esc(s.seed_artist || s.seed_title || "")}</div>
        </div>
      </div>
      <div class="resultActions">
        <button class="primaryBtn" type="button" data-act="play">Play</button>
        <button class="btn" type="button" data-act="del">Delete</button>
      </div>
    `;

    row.querySelector('[data-act="play"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        await backend.stationsPlay(s.id, true);
      } catch {}
    });

    
    row.querySelector('[data-act="del"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!confirm(`Delete station "${s.name || ""}"?`)) return;
      try {
        await backend.stationsDelete(s.id);
        await loadStations();
      } catch (e) {
        alert(`Failed to delete: ${e.message || e}`);
      }
    });
    wrap.appendChild(row);
  }
}

async function bindCreate() {
  const btn = el("stCreate");
  if (!btn || btn.__helixBound) return;
  btn.__helixBound = true;

  const seedSel = el("stSeedType");
  const titleWrap = el("stTitleWrap");
  if (seedSel && !seedSel.__helixBound) {
    seedSel.__helixBound = true;
    seedSel.addEventListener("change", () => {
      const v = (seedSel.value || "artist").toLowerCase();
      if (titleWrap) titleWrap.style.display = v === "track" ? "block" : "none";
    });
    // init
    try { seedSel.dispatchEvent(new Event("change")); } catch {}
  }

  const useNow = el("stUseNow");
  if (useNow && !useNow.__helixBound) {
    useNow.__helixBound = true;
    useNow.addEventListener("click", async () => {
      try {
        const st = await backend.playerState();
        const np = st && st.now_playing;
        if (!np) return;
        if (el("stArtist")) el("stArtist").value = np.artist || "";
        if (el("stTitle")) el("stTitle").value = np.title || "";
        if (el("stName")) {
          // helpful default name
          const t = (seedSel && seedSel.value === "track") ? `${np.title || ""} Radio` : `${np.artist || ""} Radio`;
          if (!el("stName").value) el("stName").value = t.trim();
        }
      } catch {}
    });
  }
  btn.addEventListener("click", async () => {
    const name = (el("stName")?.value || "").trim();
    const artist = (el("stArtist")?.value || "").trim();
    const seedType = (el("stSeedType")?.value || "artist").trim();
    const title = (el("stTitle")?.value || "").trim();
    const status = el("stCreateStatus");
    if (status) status.textContent = "";
    if (!name || !artist) {
      if (status) status.textContent = "Please provide both a station name and seed artist.";
      return;
    }
    if (seedType.toLowerCase() === "track" && !title) {
      if (status) status.textContent = "Please provide a seed track title for a track station.";
      return;
    }
    try {
      const created = await backend.stationsCreate({
        name,
        seed_type: seedType.toLowerCase() === "track" ? "track" : "artist",
        seed_artist: artist,
        seed_title: seedType.toLowerCase() === "track" ? title : "",
      });

      if (seedType.toLowerCase() === "track") {
        try {
          await backend.stationsPlay(created.id, true);
          window.location.href = "index.html";
          return;
        } catch {}
      }
      if (status) status.textContent = "Created.";
      el("stName").value = "";
      // keep artist field for rapid creation
      await loadStations();
    } catch (e) {
      if (status) status.textContent = `Failed: ${e.message || ""}`;
    }
  });
}

export async function init() {
  await requireAuth();
  startPlayerPolling();
  await bindCreate();
  await loadStations();
}
