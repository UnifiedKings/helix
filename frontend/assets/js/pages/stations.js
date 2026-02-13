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
  const seedInf = Math.round((s.seed_influence ?? 0.75) * 100);
  const cooldown = (s.artist_cooldown ?? 5);
  const variety = (s.artist_variety ?? 1);
  const allowAlts = !!s.allow_seed_alternates;
  const tagStrict = (s.tag_strictness ?? 70);
  const popBias = (s.popularity_bias ?? 50);
  const eraStart = (s.era_start ?? 0);
  const eraEnd = (s.era_end ?? 0);
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
          <div class="muted">Favor artist variety</div>
          <select class="input" data-f="variety">
            <option value="0" ${variety === 0 ? "selected" : ""}>Low</option>
            <option value="1" ${variety === 1 ? "selected" : ""}>Medium</option>
            <option value="2" ${variety === 2 ? "selected" : ""}>High</option>
          </select>
          <div class="muted" style="font-size:12px;">Adds a long-term penalty to frequently played artists.</div>
        </div>

        <div class="hxField">
          <div class="muted">Discoverability (0–100)</div>
          <div class="hxRow">
            <input class="input" type="range" min="0" max="100" step="1" data-f="discover" value="${esc(String(discover))}" style="flex:1;" />
            <div class="muted" data-f="discoverLabel" style="min-width:34px; text-align:right;">${esc(String(discover))}</div>
          </div>
          <div class="muted" style="font-size:12px;">Higher = more adventurous, more tag-based discovery.</div>
        </div>

        <div class="hxField">
          <div class="muted">Seed influence (0–100)</div>
          <div class="hxRow">
            <input class="input" type="range" min="0" max="100" step="1" data-f="seed" value="${esc(String(seedInf))}" style="flex:1;" />
            <div class="muted" data-f="seedLabel" style="min-width:34px; text-align:right;">${esc(String(seedInf))}</div>
          </div>
          <div class="muted" style="font-size:12px;">Higher = stay closer to the original station seed.</div>
        </div>

        <div class="hxField" style="grid-column: 1 / -1;">
          <label class="hxRow" style="cursor:pointer;">
            <input type="checkbox" data-f="allowAlts" ${allowAlts ? "checked" : ""} />
            <span>Allow alternate versions of the seed track (covers/live/remasters)</span>
          </label>
          <div class="muted" style="font-size:12px;">Recommended OFF to prevent cover spam on track stations.</div>
        </div>

        <div class="hxField" style="grid-column: 1 / -1;">
          <div class="muted">Artist blacklist (one per line, or comma-separated)</div>
          <textarea class="input" data-f="blacklist" rows="4" placeholder="e.g. Artist A\nArtist B">${esc(String(blacklist))}</textarea>
          <div class="muted" style="font-size:12px;">Never play these artists on this station.</div>
        </div>
      </div>

      <details class="hxDetails">
        <summary>Advanced options</summary>
        <div class="hxModalGrid">
          <div class="hxField">
            <div class="muted">Era filter</div>
            <select class="input" data-f="eraMode">
              <option value="any">Any era</option>
              <option value="last10">Last 10 years</option>
              <option value="custom">Custom range</option>
            </select>
            <div class="muted" style="font-size:12px;">Note: era filtering depends on available metadata.</div>
          </div>

          <div class="hxField">
            <div class="muted">Custom era range</div>
            <div class="hxRow">
              <input class="input" type="number" min="0" max="3000" step="1" data-f="eraStart" placeholder="From" value="${esc(String(eraStart || ""))}" style="width:120px;" />
              <input class="input" type="number" min="0" max="3000" step="1" data-f="eraEnd" placeholder="To" value="${esc(String(eraEnd || ""))}" style="width:120px;" />
            </div>
          </div>

          <div class="hxField">
            <div class="muted">Popularity bias (0–100)</div>
            <div class="hxRow">
              <input class="input" type="range" min="0" max="100" step="1" data-f="pop" value="${esc(String(popBias))}" style="flex:1;" />
              <div class="muted" data-f="popLabel" style="min-width:34px; text-align:right;">${esc(String(popBias))}</div>
            </div>
            <div class="muted" style="font-size:12px;">Low = popular tracks, High = deeper cuts (best-effort).</div>
          </div>

          <div class="hxField">
            <div class="muted">Tag strictness (0–100)</div>
            <div class="hxRow">
              <input class="input" type="range" min="0" max="100" step="1" data-f="tagStrict" value="${esc(String(tagStrict))}" style="flex:1;" />
              <div class="muted" data-f="tagStrictLabel" style="min-width:34px; text-align:right;">${esc(String(tagStrict))}</div>
            </div>
            <div class="muted" style="font-size:12px;">Higher = stricter genre consistency.</div>
          </div>
        </div>
      </details>

      <div class="hxModalFooter">
        <div class="muted" data-f="status" style="margin-right:auto;"></div>
        <button class="btn" type="button" data-act="cancel">Cancel</button>
        <button class="primaryBtn" type="button" data-act="save">Save</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  // initialize era mode based on stored values
  const eraModeSel = overlay.querySelector('[data-f="eraMode"]');
  if (eraModeSel) {
    if (eraStart && eraEnd) {
      eraModeSel.value = "custom";
    } else {
      eraModeSel.value = "any";
    }
  }

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
  const sSlider = overlay.querySelector('[data-f="seed"]');
  const sLab = overlay.querySelector('[data-f="seedLabel"]');
  sSlider?.addEventListener("input", () => { if (sLab) sLab.textContent = String(sSlider.value); });
  const pSlider = overlay.querySelector('[data-f="pop"]');
  const pLab = overlay.querySelector('[data-f="popLabel"]');
  pSlider?.addEventListener("input", () => { if (pLab) pLab.textContent = String(pSlider.value); });
  const tSlider = overlay.querySelector('[data-f="tagStrict"]');
  const tLab = overlay.querySelector('[data-f="tagStrictLabel"]');
  tSlider?.addEventListener("input", () => { if (tLab) tLab.textContent = String(tSlider.value); });

  overlay.querySelector('[data-act="save"]').addEventListener("click", async () => {
    const status = overlay.querySelector('[data-f="status"]');
    if (status) status.textContent = "";

    const cooldownV = clampInt(overlay.querySelector('[data-f="cooldown"]')?.value, 0, 50, 5);
    const varietyV = clampInt(overlay.querySelector('[data-f="variety"]')?.value, 0, 2, 1);
    const discoverV = clampInt(dSlider?.value, 0, 100, 35);
    const seedV = clampInt(sSlider?.value, 0, 100, 75);
    const allowV = !!overlay.querySelector('[data-f="allowAlts"]')?.checked;
    const blV = (overlay.querySelector('[data-f="blacklist"]')?.value || "");

    const eraMode = overlay.querySelector('[data-f="eraMode"]')?.value || "any";
    let eraStartV = clampInt(overlay.querySelector('[data-f="eraStart"]')?.value, 0, 3000, 0);
    let eraEndV = clampInt(overlay.querySelector('[data-f="eraEnd"]')?.value, 0, 3000, 0);
    if (eraMode === "any") {
      eraStartV = 0;
      eraEndV = 0;
    } else if (eraMode === "last10") {
      const year = new Date().getFullYear();
      eraStartV = year - 10;
      eraEndV = year;
    } else {
      // custom: allow 0/0 to mean any, but keep user inputs if present
    }

    const popV = clampInt(pSlider?.value, 0, 100, 50);
    const tagV = clampInt(tSlider?.value, 0, 100, 70);

    const patch = {
      discovery: clampFloat(discoverV / 100, 0, 1, 0.35),
      seed_influence: clampFloat(seedV / 100, 0, 1, 0.75),
      artist_cooldown: cooldownV,
      artist_variety: varietyV,
      allow_seed_alternates: allowV,
      era_start: eraStartV,
      era_end: eraEndV,
      popularity_bias: popV,
      tag_strictness: tagV,
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
        <button class="btn" type="button" data-act="opts">Options</button>
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

    row.querySelector('[data-act="opts"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      openStationOptionsModal(s, async (patch) => {
        await backend.stationsUpdate(s.id, patch);
        await loadStations();
      });
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
