import * as backend from "../api/backend.js";
import { startPlayerPolling } from "../player.js";
import { requireAuth } from "../auth-guard.js";
import {showLoading, hideLoading} from "../ui/loading.js"


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

function clampInt(v, min, max, fallback) {
  const n = parseInt(v, 10);
  if (Number.isFinite(n)) return Math.min(max, Math.max(min, n));
  return fallback;
}

function clampFloat(v, min, max, fallback) {
  const n = parseFloat(v);
  if (Number.isFinite(n)) return Math.min(max, Math.max(min, n));
  return fallback;
}

function openStationOptionsModal(station, onSave) {
  // remove any existing modal
  const existing = document.querySelector(".hxModalOverlay");
  if (existing) existing.remove();

  const s = station || {};
  const discover = Math.round((s.discovery ?? 0.35) * 100);
  const cooldown = (s.artist_cooldown ?? 5);
  const blacklist = (s.artist_blacklist ?? "");

  const overlay = document.createElement("div");
  overlay.className = "hxModalOverlay";
  overlay.innerHTML = `
    <div class="hxModal" role="dialog" aria-modal="true" aria-label="Station options">
      <div class="hxModalHeader">
        <div>
          <div class="hxModalTitle">Station options</div>
          <div class="muted">${esc(s.name || "")}</div>
        </div>
        <button class="btn" type="button" data-act="close">Close</button>
      </div>

      <div class="hxModalGrid">
        <div class="hxField">
          <div class="muted">Artist cooldown (tracks)</div>
          <input class="input" type="number" min="0" max="50" step="1" data-f="cooldown" value="${esc(String(cooldown))}" />
          <div class="muted" style="font-size:12px;">Don't repeat the same artist within X tracks.</div>
        </div>

        <div class="hxField">
          <div class="muted">Discoverability (0–100)</div>
          <div class="hxRow">
            <input class="input" type="range" min="0" max="100" step="1" data-f="discover" value="${esc(String(discover))}" style="flex:1;" />
            <div class="muted" data-f="discoverLabel" style="min-width:34px; text-align:right;">${esc(String(discover))}</div>
          </div>
          <div class="muted" style="font-size:12px;">Higher = more likely to choose less-similar artists.</div>
        </div>

        <div class="hxField" style="grid-column: 1 / -1;">
          <div class="muted">Artist blacklist (one per line, or comma-separated)</div>
          <textarea class="input" data-f="blacklist" rows="4" placeholder="e.g. Artist A\nArtist B">${esc(String(blacklist))}</textarea>
          <div class="muted" style="font-size:12px;">Never play these artists on this station.</div>
        </div>
      </div>

      <div class="hxModalFooter">
        <div class="muted" data-f="status" style="margin-right:auto;"></div>
        <button class="btn" type="button" data-act="cancel">Cancel</button>
        <button class="primaryBtn" type="button" data-act="save">Save</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  overlay.querySelector('[data-act="close"]').addEventListener("click", close);
  overlay.querySelector('[data-act="cancel"]').addEventListener("click", close);

  // live labels
  const dSlider = overlay.querySelector('[data-f="discover"]');
  const dLab = overlay.querySelector('[data-f="discoverLabel"]');
  dSlider?.addEventListener("input", () => { if (dLab) dLab.textContent = String(dSlider.value); });

  overlay.querySelector('[data-act="save"]').addEventListener("click", async () => {
    const status = overlay.querySelector('[data-f="status"]');
    if (status) status.textContent = "";

    const cooldownV = clampInt(overlay.querySelector('[data-f="cooldown"]')?.value, 0, 50, 5);
    const discoverV = clampInt(dSlider?.value, 0, 100, 35);
    const blV = (overlay.querySelector('[data-f="blacklist"]')?.value || "");

    const patch = {
      discovery: clampFloat(discoverV / 100, 0, 1, 0.35),
      artist_cooldown: cooldownV,
      artist_blacklist: blV,
    };

    try {
      if (status) status.textContent = "Saving...";
      await onSave(patch);
      close();
    } catch (e) {
      if (status) status.textContent = `Failed: ${e.message || e}`;
    }
  });
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
    row.className = "stationCard";

    const thumbUrl = (s.thumbnail_url || "").trim();

    // Apply thumbnail as card background
    if (thumbUrl) {
      row.style.backgroundImage = `url("${thumbUrl}")`;
    } else {
      row.style.backgroundImage = "";
    }
    const seed = esc(s.seed_artist || s.seed_title || "");
    const initial = (s.name || "?").trim().slice(0, 1).toUpperCase();

    row.innerHTML = `
      <div class="stationMeta">
        <div class="title">${esc(s.name || "")}</div>
        <div class="subtitle">Seed: ${seed}</div>
      </div>
        <div class="stationActions">
          <button class="btnPlay" type="button" data-act="play">Play</button>
          <button class="btnOptions" type="button" data-act="opts">Options</button>
          <button class="btnDelete" type="button" data-act="del">Delete</button>
        </div>
      </div>
    `;
row.addEventListener("click", async (ev) => {
      const t = ev.target;
      if (t && (t.closest && t.closest("button"))) return;
      try {
        await playStation(s.id);
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {}
    });

    const _btnPlay = row.querySelector('[data-act="play"]');
    if (_btnPlay) _btnPlay.addEventListener("click", async (ev) => {
      console.log("Play station button detected")
      ev.preventDefault();
      ev.stopPropagation();
      try {
        await playStation(s.id);
        // Force immediate player sync when starting playback from Stations page.
        document.dispatchEvent(new CustomEvent("helix-player-refresh", {
          detail: { forceLoadStream: true }
        }));
      } catch {}
    });

    const _btnOpts = row.querySelector('[data-act="opts"]');
    if (_btnOpts) _btnOpts.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      openStationOptionsModal(s, async (patch) => {
        await backend.stationsUpdate(s.id, patch);
        await loadStations();
      });
    });

    
    const _btnDel = row.querySelector('[data-act="del"]');
    if (_btnDel) _btnDel.addEventListener("click", async (ev) => {
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
          await playStation(created.id);
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

function escapeHtml(s){
  return String(s ?? "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#39;");
}

export async function init() {
  await requireAuth();
  startPlayerPolling();
  await bindCreate();
  await loadStations();
}

async function playStation(id)
{
  console.log("Playing station " + id)
  const st = el("stPlayStatus");
  if (st) st.textContent = "";
  showLoading("Starting station...")
  // Failsafe: never let the loading overlay hang forever.
  const failSafe = setTimeout(() => {
    try { hideLoading(); } catch {}
    try { if (st && !st.textContent) {
        st.textContent = "Timed out starting station. Please try again.";
        try { st.style.color = "#ff8b8b"; } catch {}
      }
      try { alert("Timed out starting station. Please try again."); } catch {} } catch {}
  }, 8000);
  try{
    await backend.stationsPlay(id, true);
    document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
    // Hide loading immediately; the player state may not update if generation fails or is delayed.
    hideLoading();
    clearTimeout(failSafe);
  } catch (e) {
    hideLoading();
    clearTimeout(failSafe);
    const msg = (e && e.message) ? e.message : String(e || "Failed to start station");
    if (st) {
      st.textContent = msg;
      try { st.style.color = "#ff8b8b"; } catch {}
    }
    // Always show a visible pop-up too, since the status line can be missed depending on layout.
    try { alert(msg); } catch {}
    throw e;
  }
}
// renderStations injected for grid UI