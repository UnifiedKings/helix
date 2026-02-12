import * as backend from "./api/backend.js";

// ---- Persist last known player state so UI can restore after hard refresh even if backend is slow ----
const LAST_STATE_KEY = "helix_last_player_state_v1";

function saveLastState(state) {
  try { localStorage.setItem(LAST_STATE_KEY, JSON.stringify(state)); } catch {}
}

function loadLastState() {
  try {
    const raw = localStorage.getItem(LAST_STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}


// -----------------------------------------------------------------------------
// SINGLETON AUDIO + PLAYER STATE
//
// This script can be loaded on multiple pages. If it gets included twice on the
// same document (or re-evaluated by a hot-reload / partial navigation), we MUST
// not create multiple <audio> elements or duplicate event bindings.
//
// We store a small state bundle on window to guarantee a single audio element
// and a single polling loop per browser tab.
// -----------------------------------------------------------------------------

function getGlobalState() {
  if (!window.__HELIX_PLAYER_STATE__) {
    window.__HELIX_PLAYER_STATE__ = {
      audio: null,
      lastNowId: null,
      pollTimer: null,
    };
  }
  return window.__HELIX_PLAYER_STATE__;
}

function getOrCreateAudio() {
  const gs = getGlobalState();

  // Prefer the existing instance, if any.
  if (gs.audio) return gs.audio;

  // If an element exists in DOM (e.g., script reloaded), reuse it.
  let el = document.getElementById("helix-audio");
  if (el && el.tagName && el.tagName.toLowerCase() === "audio") {
    gs.audio = el;
    return gs.audio;
  }

  // Defensive cleanup: if multiple audio tags exist (bug), pause them.
  document.querySelectorAll("audio").forEach((a) => {
    try {
      if (a.id !== "helix-audio") a.pause();
    } catch {}
  });

  el = document.createElement("audio");
  el.id = "helix-audio";
  el.preload = "auto";
  el.style.display = "none";
  document.body.appendChild(el);
  gs.audio = el;
  return gs.audio;
}

function qs(id) {
  return document.getElementById(id);
}

function setText(id, v) {
  const el = qs(id);
  if (el) el.textContent = v || "";
}

function setImg(id, src) {
  const el = qs(id);
  if (!el) return;
  if (!src) {
    el.removeAttribute("src");
    return;
  }
  el.src = src;
}

function setBuffering(isBuf) {
  const gs = getGlobalState();
  gs.__isBuffering = !!isBuf;
  const base = (gs.__pbMetaBase || "");
  const meta = base ? (gs.__isBuffering ? `${base} • Loading…` : base) : (gs.__isBuffering ? "Loading…" : "");
  setText("pbMeta", meta);
  // Also reflect on Now Playing header if present
  const hero = document.getElementById("npHeroSub");
  if (hero && gs.__isBuffering) {
    hero.setAttribute("data-helix-loading", "1");
  } else if (hero) {
    hero.removeAttribute("data-helix-loading");
  }
}

function formatTime(sec) {
  if (!isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

// Deterministic pseudo-waveform (visual only).
function makeWaveformValues(key, n = 120) {
  const seedStr = String(key || "");
  // simple xorshift32 seeded from hash
  let h = 2166136261 >>> 0;
  for (let i = 0; i < seedStr.length; i++) {
    h ^= seedStr.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  let x = (h || 1) >>> 0;
  const vals = new Array(n);
  for (let i = 0; i < n; i++) {
    // xorshift32
    x ^= (x << 13) >>> 0;
    x ^= (x >>> 17) >>> 0;
    x ^= (x << 5) >>> 0;
    // 0..1
    const u = (x >>> 0) / 4294967295;
    // shape: bias towards middle, add gentle peaks
    const shaped = Math.pow(u, 0.55) * 0.85 + 0.15;
    vals[i] = shaped;
  }
  return vals;
}

function drawWave(canvas, values, progress01) {
  if (!canvas) return;
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;

  // Render in device pixels but keep math aligned with the displayed width.
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 1;
  const cssH = canvas.clientHeight || canvas.height || 30;

  const w = Math.max(1, Math.floor(cssW * dpr));
  const h = Math.max(1, Math.floor(cssH * dpr));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;

  const n = values && values.length ? values.length : 0;

  // colors pulled from CSS palette (approx)
  const base = "#3a4150";
  const played = "#2d6cdf";

  ctx.clearRect(0, 0, w, h);
  if (!n) return;

  // Bars span the FULL canvas width (no centering). This makes click-to-seek
  // positions line up with the blue "played" bars.
  const gap = Math.max(1, Math.floor(1 * dpr));
  const barW = Math.max(1, Math.floor((w - (n - 1) * gap) / n));

  const mid = h / 2;
  const maxAmp = h * 0.42;

  const p = Math.min(1, Math.max(0, progress01 || 0));
  const playedX = p * w; // device pixels

  for (let i = 0; i < n; i++) {
    const amp = values[i] * maxAmp;
    const x = i * (barW + gap);
    const y = mid - amp;
    const hh = amp * 2;

    // Mark as played if the bar's CENTER is before the played position.
    const barCenter = x + barW / 2;
    ctx.fillStyle = barCenter <= playedX ? played : base;
    ctx.fillRect(x, y, barW, hh);
  }
}

function ensureProgressBindings(audio) {
  const gs = getGlobalState();
  if (gs.__progressBound) return;
  gs.__progressBound = true;

  const trackEl = document.getElementById("pbTrack");
  const canvas = document.getElementById("pbWave");
  const curEl = document.getElementById("pbCur");
  const durEl = document.getElementById("pbDur");

  let isDragging = false;

  function updateUI() {
    if (!audio) return;
    const dur = audio.duration || 0;
    const cur = audio.currentTime || 0;
    if (curEl) curEl.textContent = formatTime(cur);
    if (durEl) durEl.textContent = formatTime(dur);
    const p = dur > 0 ? cur / dur : 0;
    if (trackEl) trackEl.setAttribute("aria-valuenow", String(Math.round(p * 100)));
    drawWave(canvas, gs.__waveValues || [], p);
  }

  function seekToClientX(clientX) {
    if (!trackEl || !audio) return;
    const rect = trackEl.getBoundingClientRect();
    const x = Math.min(rect.right, Math.max(rect.left, clientX));
    const ratio = rect.width > 0 ? (x - rect.left) / rect.width : 0;
    if (isFinite(audio.duration) && audio.duration > 0) {
      audio.currentTime = ratio * audio.duration;
      updateUI();
    }
  }

  if (trackEl && !trackEl.__helixBound) {
    trackEl.__helixBound = true;

    trackEl.addEventListener("mousedown", (e) => {
      isDragging = true;
      seekToClientX(e.clientX);
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      seekToClientX(e.clientX);
    });
    window.addEventListener("mouseup", () => { isDragging = false; });

    trackEl.addEventListener("touchstart", (e) => {
      isDragging = true;
      if (e.touches && e.touches[0]) seekToClientX(e.touches[0].clientX);
    }, { passive: true });
    window.addEventListener("touchmove", (e) => {
      if (!isDragging) return;
      if (e.touches && e.touches[0]) seekToClientX(e.touches[0].clientX);
    }, { passive: true });
    window.addEventListener("touchend", () => { isDragging = false; });

    // keyboard seek
    trackEl.addEventListener("keydown", (e) => {
      if (!audio || !isFinite(audio.duration) || audio.duration <= 0) return;
      const step = e.shiftKey ? 10 : 5;
      if (e.key === "ArrowLeft") { audio.currentTime = Math.max(0, audio.currentTime - step); updateUI(); e.preventDefault(); }
      if (e.key === "ArrowRight") { audio.currentTime = Math.min(audio.duration, audio.currentTime + step); updateUI(); e.preventDefault(); }
      if (e.key === "Home") { audio.currentTime = 0; updateUI(); e.preventDefault(); }
      if (e.key === "End") { audio.currentTime = audio.duration; updateUI(); e.preventDefault(); }
    });
  }

  // keep UI in sync
  audio.addEventListener("timeupdate", updateUI);
  audio.addEventListener("loadedmetadata", updateUI);
  audio.addEventListener("durationchange", updateUI);
  audio.addEventListener("seeked", updateUI);
  audio.addEventListener("play", updateUI);
  audio.addEventListener("pause", updateUI);

  // Buffering / loading UI
  audio.addEventListener("loadstart", () => setBuffering(true));
  audio.addEventListener("waiting", () => setBuffering(true));
  audio.addEventListener("stalled", () => setBuffering(true));
  audio.addEventListener("canplay", () => setBuffering(false));
  audio.addEventListener("playing", () => setBuffering(false));
  audio.addEventListener("error", () => setBuffering(false));

  // paint once
  requestAnimationFrame(updateUI);

  gs.__updateProgressUI = updateUI;
}

function clamp01(v) {
  if (!isFinite(v)) return 1;
  return Math.min(1, Math.max(0, v));
}

function ensureVolumeBindings(audio) {
  const gs = getGlobalState();
  if (gs.__volumeBound) return;
  gs.__volumeBound = true;

  const slider = qs("pbVol");
  const icon = qs("pbVolIcon");
  if (!slider && !icon) return;

  function setVol(vol01) {
    const v = clamp01(vol01);
    audio.volume = v;
    try { localStorage.setItem("helix_volume", String(v)); } catch {}
    if (slider) slider.value = String(Math.round(v * 100));
    updateIcon();
  }

  function updateIcon() {
    if (!icon) return;
    const v = clamp01(audio.volume);
    // simple 3-state icon
    if (v <= 0.001) icon.textContent = "🔇";
    else if (v < 0.5) icon.textContent = "🔉";
    else icon.textContent = "🔊";
  }

  // Initialize from storage.
  let initial = 1;
  try {
    const raw = localStorage.getItem("helix_volume");
    if (raw != null) {
      const parsed = parseFloat(raw);
      if (isFinite(parsed)) initial = parsed;
    }
  } catch {}

  initial = clamp01(initial);
  gs.__lastNonZeroVol = initial > 0.001 ? initial : 1;
  audio.volume = initial;
  if (slider) slider.value = String(Math.round(initial * 100));
  updateIcon();

  if (slider && !slider.__helixBound) {
    slider.__helixBound = true;
    slider.addEventListener("input", () => {
      const v = clamp01(parseFloat(slider.value) / 100);
      if (v > 0.001) gs.__lastNonZeroVol = v;
      setVol(v);
    });
  }

  if (icon && !icon.__helixBound) {
    icon.__helixBound = true;
    icon.addEventListener("click", () => {
      const v = clamp01(audio.volume);
      if (v <= 0.001) {
        setVol(gs.__lastNonZeroVol || 1);
      } else {
        gs.__lastNonZeroVol = v;
        setVol(0);
      }
    });
  }

  audio.addEventListener("volumechange", () => {
    const v = clamp01(audio.volume);
    if (slider) slider.value = String(Math.round(v * 100));
    if (v > 0.001) gs.__lastNonZeroVol = v;
    updateIcon();
  });
}

function bindButtons() {
  const bReplay = qs("pbReplay");
  const bPrev = qs("pbPrev");
  const bPlay = qs("pbPlay");
  const bNext = qs("pbNext");
  const bLike = qs("pbLike");
  const bDislike = qs("pbDislike");
  const ap = document.getElementById("autoplayToggle");

  if (bReplay && !bReplay.__helixBound) {
    bReplay.__helixBound = true;
    bReplay.addEventListener("click", async () => {
      const audio = getOrCreateAudio();
      if (!audio) return;
      audio.currentTime = 0;
      try { await backend.playerResume(); } catch {}
      audio.play().catch(() => {});
    });
  }
  if (bPrev && !bPrev.__helixBound) {
    bPrev.__helixBound = true;
    bPrev.addEventListener("click", async () => { await backend.playerPrev(); await syncOnce(true); });
  }
  if (bNext && !bNext.__helixBound) {
    bNext.__helixBound = true;
    bNext.addEventListener("click", async () => { await backend.playerNext(); await syncOnce(true); });
  }
  if (bPlay && !bPlay.__helixBound) {
    bPlay.__helixBound = true;
    bPlay.addEventListener("click", async () => {
      const st = await backend.playerState();
      if (st.is_playing) {
        await backend.playerPause();
        const audio = getOrCreateAudio();
        if (audio) audio.pause();
      } else {
        await backend.playerResume();
        const audio = getOrCreateAudio();
        if (audio) audio.play().catch(() => {});
      }
      await syncOnce(false);
    });
  }

  if (ap && !ap.__helixBound) {
    ap.__helixBound = true;
    ap.addEventListener("change", async () => {
      try { await backend.playerSetAutoplay(!!ap.checked); } catch {}
      await syncOnce(false);
    });
  }

  if (bLike && !bLike.__helixBound) {
    bLike.__helixBound = true;
    bLike.addEventListener("click", async () => {
      const st = await backend.playerState();
      const np = st && st.now_playing;
      if (!np) return;
      try {
        const res = await backend.likesToggle({
          title: np.title,
          artist: np.artist,
          album: np.album || "",
          duration_ms: np.duration_ms || 0,
          art_url: np.art_url || "",
          source: np.source || "",
          subsonic_song_id: np.subsonic_song_id || null,
          yt_video_id: np.yt_video_id || null,
        });
        bLike.textContent = (res && res.liked) ? "♥" : "♡";
      } catch {}
    });
  }

  if (bDislike && !bDislike.__helixBound) {
    bDislike.__helixBound = true;
    bDislike.addEventListener("click", async () => {
      const st = await backend.playerState();
      const np = st && st.now_playing;
      if (!np) return;
      try {
        const res = await backend.dislikesToggle({
          title: np.title,
          artist: np.artist,
          album: np.album || "",
          duration_ms: np.duration_ms || 0,
          art_url: np.art_url || "",
          source: np.source || "",
          subsonic_song_id: np.subsonic_song_id || null,
          yt_video_id: np.yt_video_id || null,
        });

        // Visually toggle
        bDislike.textContent = (res && res.disliked) ? "🚫" : "👎";
        // If user disliked the currently playing song, immediately skip it.
        if (res && res.disliked) {
          try { await backend.playerNext(); } catch {}
          await syncOnce(true);
        } else {
          await syncOnce(false);
        }
      } catch {}
    });
  }
}

function renderQueue(queue, currentIndex) {
  const list = qs("npQueueList");
  if (!list) return;
  list.innerHTML = "";

  const start = Math.max(0, Number.isFinite(currentIndex) ? currentIndex : 0);
  const full = queue || [];
  const visible = full.slice(start);

  visible.forEach((q, visIdx) => {
    const realIdx = start + visIdx;
    const row = document.createElement("div");
    // In the visible list, the currently playing item is always the first row.
    row.className = "npQueueItem" + (visIdx === 0 ? " active" : "");
    row.innerHTML = `
      <div class="left">
        <div class="t">${q.title || ""}</div>
        <div class="a">${q.artist || ""}</div>
      </div>
      <button class="resultMenuBtn npQueueMenuBtn" type="button" aria-label="Queue menu" title="More">⋯</button>
    `;

    row.addEventListener("click", async () => {
      try {
        await backend.playerJump(realIdx);
        await syncOnceWithRetry(true);
      } catch {}
    });

    const menuBtn = row.querySelector(".npQueueMenuBtn");
    if (menuBtn) {
      menuBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openQueueMenu(menuBtn, q);
      });
    }

    list.appendChild(row);
  });

  setText("npQueueCount", `${visible.length} songs`);
}

let __openQueueMenuEl = null;

function closeOpenQueueMenu() {
  if (__openQueueMenuEl) {
    __openQueueMenuEl.remove();
    __openQueueMenuEl = null;
  }
}

function openQueueMenu(anchorBtn, queueItem) {
  closeOpenQueueMenu();

  const menu = document.createElement("div");
  menu.className = "resultMenu";
  menu.innerHTML = `
    <button type="button" class="resultMenuItem" data-act="remove">Remove from queue</button>
  `;

  const rect = anchorBtn.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;
  const desiredLeft = Math.round(rect.right - 220);
  const maxLeft = Math.max(8, window.innerWidth - 228);
  menu.style.left = `${Math.min(maxLeft, Math.max(8, desiredLeft))}px`;
  menu.style.zIndex = "9999";

  menu.addEventListener("click", async (ev) => {
    const btn = ev.target.closest?.("button[data-act]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    try {
      await backend.playerQueueRemoveItem(queueItem.id);
      await syncOnceWithRetry(true);
    } catch (e) {
      console.error(e);
    } finally {
      closeOpenQueueMenu();
    }
  });

  document.body.appendChild(menu);
  __openQueueMenuEl = menu;

  const onDocDown = (ev) => {
    if (ev.target === anchorBtn) return;
    if (menu.contains(ev.target)) return;
    closeOpenQueueMenu();
    document.removeEventListener("mousedown", onDocDown, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const onKey = (ev) => {
    if (ev.key === "Escape") {
      closeOpenQueueMenu();
      document.removeEventListener("mousedown", onDocDown, true);
      document.removeEventListener("keydown", onKey, true);
    }
  };
  document.addEventListener("mousedown", onDocDown, true);
  document.addEventListener("keydown", onKey, true);
}

function updatePlayerBar(st) {
  const np = st.now_playing;
  if (!np) return;

  // waveform for progress bar (visual)
  const gs = getGlobalState();
  if (np.id && np.id !== gs.__waveKey) {
    gs.__waveKey = np.id;
    gs.__waveValues = makeWaveformValues(np.id, 120);
  }

  // Player bar
  setText("pbTitle", np.title);
  setText("pbSub", np.artist);
  const gs2 = getGlobalState();
  gs2.__pbMetaBase = (np.album ? np.album : "");
  setText("pbMeta", gs2.__isBuffering ? (gs2.__pbMetaBase ? `${gs2.__pbMetaBase} • Loading…` : "Loading…") : gs2.__pbMetaBase);
  setImg("pbThumb", np.art_url || "");

  // Now Playing page
  setText("npTitle", np.title);
  setText("npArtist", np.artist);
  setText("npHeroTitle", np.title);
  setText("npHeroSub", np.album ? `${np.artist} — ${np.album}` : np.artist);
  setImg("npThumb", np.art_url || "");
  setImg("npHeroImg", np.art_url || "");

  renderQueue(st.queue, st.current_index);

  // play/pause icon
  const bPlay = qs("pbPlay");
  if (bPlay) bPlay.textContent = st.is_playing ? "⏸" : "▶";

  // autoplay toggle UI (Now Playing page)
  const ap = document.getElementById("autoplayToggle");
  const apTitle = document.querySelector(".npToggleTitle");
  if (ap) ap.checked = !!st.autoplay_enabled;
  if (apTitle) apTitle.textContent = st.autoplay_enabled ? "Autoplay is on" : "Autoplay is off";

  // like button
  const likeBtn = document.getElementById("pbLike");
  const dislikeBtn = document.getElementById("pbDislike");
  const gs3 = getGlobalState();
  const likeKey = (np.subsonic_song_id ? `subsonic:${np.subsonic_song_id}` : (np.yt_video_id ? `yt:${np.yt_video_id}` : ""));
  if (likeBtn && likeKey && gs3.__lastLikeKey !== likeKey) {
    gs3.__lastLikeKey = likeKey;
    likeBtn.textContent = "♡";
    backend.likesIsLiked({ yt_video_id: np.yt_video_id || null, subsonic_song_id: np.subsonic_song_id || null })
      .then((r) => { likeBtn.textContent = r && r.liked ? "♥" : "♡"; })
      .catch(() => { likeBtn.textContent = "♡"; });
  }

  // dislike button
  if (dislikeBtn && likeKey && gs3.__lastDislikeKey !== likeKey) {
    gs3.__lastDislikeKey = likeKey;
    dislikeBtn.textContent = "👎";
    backend.dislikesIsDisliked({ yt_video_id: np.yt_video_id || null, subsonic_song_id: np.subsonic_song_id || null })
      .then((r) => { dislikeBtn.textContent = r && r.disliked ? "🚫" : "👎"; })
      .catch(() => { dislikeBtn.textContent = "👎"; });
  }

  // Station mode header (Now Playing page)
  const modeWrap = document.getElementById("npMode");
  if (modeWrap) {
    const active = st.active_station && st.active_station_id;
    if (active) {
      modeWrap.style.display = "block";
      const s = st.active_station;
      const nameEl = document.getElementById("npStationName");
      const seedEl = document.getElementById("npStationSeed");
      const metaEl = document.getElementById("npStationMeta");
      if (nameEl) nameEl.textContent = s.name || "Station";
      if (seedEl) {
        const seed = s.seed_type === "track" ? `${s.seed_title || ""} — ${s.seed_artist || ""}` : (s.seed_artist || "");
        seedEl.textContent = seed;
      }
      if (metaEl) {
        const dPct = Math.round((s.discovery || 0.35) * 100);
        metaEl.textContent = `Discovery: ${dPct}%`;
      }
    } else {
      modeWrap.style.display = "none";
    }
  }
}

async function syncOnce(forceLoadStream) {
  const gs = getGlobalState();
  const st = await backend.playerState();
  // Cache latest good state for fast restore on hard refresh.
  try { localStorage.setItem("helix_last_player_state", JSON.stringify(st)); } catch {}
  bindButtons();

  const np = st.now_playing;
  if (!np) {
    // nothing queued
    return;
  }

  updatePlayerBar(st);

  const audio = getOrCreateAudio();
  if (!audio) return;

  ensureProgressBindings(audio);
  ensureVolumeBindings(audio);

  // Bind ended handler once per audio element.
  if (!audio.__helixEndedBound) {
    audio.__helixEndedBound = true;
    audio.addEventListener("ended", async () => {
      try {
        // Natural completion (not a user skip). Let backend log as "completed".
        // We don't care about played_ms per your preference, but passing it is harmless.
        const posMs = Number.isFinite(audio.currentTime) ? Math.floor(audio.currentTime * 1000) : null;
        await backend.playerEnded(posMs);
        await syncOnceWithRetry(true);
      } catch {}
    });
  }

  const shouldLoad = forceLoadStream || (np.id && np.id !== gs.lastNowId);
  gs.lastNowId = np.id;

  if (shouldLoad) {
    // Always stream from backend so browser doesn't need Subsonic credentials.
    audio.src = backend.playerStreamUrl(np.id);
    // Force reload even if the URL is similar / cached.
    try { audio.load(); } catch {}
  }

  try { const fn = getGlobalState().__updateProgressUI; if (fn) fn(); } catch {}

  if (st.is_playing) {
    audio.play().catch(() => {});
  } else {
    audio.pause();

  }
}

async function syncOnceWithRetry(forceLoadStream) {
  const delays = [0, 200, 800, 2000];
  let lastErr = null;

  for (const d of delays) {
    if (d) await new Promise(r => setTimeout(r, d));
    try {
      await syncOnce(forceLoadStream);
      return;
    } catch (e) {
      lastErr = e;
    }
  }

  // If backend is temporarily unavailable, fall back to cached UI state.
  try {
    const raw = localStorage.getItem("helix_last_player_state");
    if (raw) {
      const st = JSON.parse(raw);
      bindButtons();
      if (st && st.now_playing) updatePlayerBar(st);
    }
  } catch {}

  throw lastErr;
}

export function startPlayerPolling() {
  const gs = getGlobalState();

  // Call once immediately.
  //
  // We intentionally do NOT constantly poll the backend for play-state. The
  // browser already knows whether it's playing (via the <audio> element), and
  // hammering /api/player/state creates unnecessary load and log spam.
  //
  // Instead we refresh state:
  //  - on initial load
  //  - after user actions (prev/next/play/pause/jump call syncOnce)
  //  - when the tab becomes visible again (in case another device changed queue)
  syncOnceWithRetry(false).catch(() => {});

  // If an older build left a poll timer running, stop it.
  if (gs.pollTimer) {
    clearInterval(gs.pollTimer);
    gs.pollTimer = null;
  }

  if (!gs.__visibilityBound) {
    gs.__visibilityBound = true;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) syncOnce(false).catch(() => {});
    });
    window.addEventListener("focus", () => syncOnce(false).catch(() => {}));
  }

  // Programmatic refresh hook for pages that start playback (search results,
  // album play buttons, etc.). PJAX navigation keeps the same JS context, so we
  // use an in-tab event to tell the player to load the new stream immediately.
  if (!gs.__refreshEventBound) {
    gs.__refreshEventBound = true;
    document.addEventListener("helix-player-refresh", (ev) => {
      const force = !!(ev && ev.detail && ev.detail.forceLoadStream);
      syncOnce(force).catch(() => {});
    });
  }
}
