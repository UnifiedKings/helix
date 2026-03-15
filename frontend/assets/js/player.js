import * as backend from "./api/backend.js";
import * as icons from "./ui/icons.js";

// ---- Persist last known player state so UI can restore after hard refresh even if backend is slow ----
const LAST_STATE_KEY = "helix_last_player_state_v1";


// ---- Autoplay rules ----
// Autoplay unless the user explicitly paused. Exception: the first page load in a tab should NOT autoplay.
const USER_PAUSED_KEY = "helix_user_paused_v1";
const FIRST_LOAD_KEY = "helix_first_load_done_v1";

function setUserPaused(v) {
  try { localStorage.setItem(USER_PAUSED_KEY, v ? "1" : "0"); } catch {}
}
function getUserPaused() {
  try { return localStorage.getItem(USER_PAUSED_KEY) === "1"; } catch { return false; }
}


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
// -----------------------------------------------------------------------------

function getGlobalState() {
  if (!window.__HELIX_PLAYER_STATE__) {
    window.__HELIX_PLAYER_STATE__ = {
      audio: null,
      lastNowKey: null,
      pollTimer: null,
      postRefreshForId: null,
      postRefreshTimer: null,
      __allowAutoplayOnce: false,
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
}

function formatTime(sec) {
  if (!isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}



function markUserInitiatedPlayback() {
  const gs = getGlobalState();
  gs.__allowAutoplayOnce = true;
  gs.__isFirstLoad = false; // a user gesture occurred; no longer treat as first-load gate
}


async function waitForNowPlayingChange(prevKey, timeoutMs = 15000) {
  const start = Date.now();
  let last = null;

  while (Date.now() - start < timeoutMs) {
    try {
      const st = await backend.playerState();
      last = st;
      const np = st && st.now_playing ? st.now_playing : null;
      const key = nowPlayingKey(np) || (np && np.id ? String(np.id) : null);
      if (np && key && key !== prevKey) return st;
      // If nothing is playing yet, keep waiting.
    } catch (e) {
      // ignore transient errors while waiting
    }
    await new Promise(r => setTimeout(r, 350));
  }
  return last; // may be unchanged/null; caller decides
}

function safePlay(audio) {
  try {
    const p = audio.play();
    if (p && typeof p.catch === "function") {
      p.catch((e) => {
        // Don't spam; most failures are non-fatal (e.g., no user gesture on first load).
        try { console.warn("Helix audio.play() failed:", e); } catch {}
      });
    }
  } catch (e) {
    try { console.warn("Helix audio.play() threw:", e); } catch {}
  }
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
    // shape: bias towards middle
    const shaped = Math.pow(u, 0.55) * 0.85 + 0.15;
    vals[i] = shaped;
  }
  return vals;
}

function drawWave(canvas, values, progress01) {
  if (!canvas) return;
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 1;
  const cssH = canvas.clientHeight || canvas.height || 30;

  const w = Math.max(1, Math.floor(cssW * dpr));
  const h = Math.max(1, Math.floor(cssH * dpr));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;

  const n = values && values.length ? values.length : 0;

  const base = "#3a4150";
  const played = "#2d6cdf";

  ctx.clearRect(0, 0, w, h);
  if (!n) return;

  const gap = Math.max(1, Math.floor(1 * dpr));
  const barW = Math.max(1, Math.floor((w - (n - 1) * gap) / n));

  const mid = h / 2;
  const maxAmp = h * 0.42;

  const p = Math.min(1, Math.max(0, progress01 || 0));
  const playedX = p * w;

  for (let i = 0; i < n; i++) {
    const amp = values[i] * maxAmp;
    const x = i * (barW + gap);
    const y = mid - amp;
    const hh = amp * 2;
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
    const np = (gs && gs.__lastNowPlaying) ? gs.__lastNowPlaying : null;
    const dur = (np && np.duration_ms ? (np.duration_ms / 1000) : (audio.duration || 0)) || 0;
    const seekable = (np && np.seekable_ms ? (np.seekable_ms / 1000) : dur) || dur;
    const cur = audio.currentTime || 0;
    if (curEl) curEl.textContent = formatTime(cur);
    if (durEl) durEl.textContent = formatTime(dur);
    const p = dur > 0 ? cur / dur : 0;
    if (trackEl) trackEl.setAttribute("aria-valuenow", String(Math.round(p * 100)));
    drawWave(canvas, gs.__waveValues || [], p);
    // Keep play/pause icon in sync with real audio state (prevents flicker on track changes).
    const playBtn = qs("pbPlay");
    if (playBtn) playBtn.textContent = audio.paused ? "▶" : "⏸";
  }

  function seekToClientX(clientX) {
    if (!trackEl || !audio) return;
    const rect = trackEl.getBoundingClientRect();
    const x = Math.min(rect.right, Math.max(rect.left, clientX));
    const ratio = rect.width > 0 ? (x - rect.left) / rect.width : 0;
    // Prefer backend-reported duration/seekable window; fallback to media element duration.
    const np = (gs && gs.__lastNowPlaying) ? gs.__lastNowPlaying : null;
    const dur = (np && np.duration_ms ? (np.duration_ms / 1000) : (audio.duration || 0)) || 0;
    const seekable = (np && np.seekable_ms ? (np.seekable_ms / 1000) : dur) || dur;
    if (isFinite(dur) && dur > 0) {
      let target = ratio * dur;
      if (isFinite(seekable) && seekable > 0) target = Math.min(target, seekable);
      audio.currentTime = target;
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

    trackEl.addEventListener("keydown", (e) => {
      if (!audio) return;
      const np = (gs && gs.__lastNowPlaying) ? gs.__lastNowPlaying : null;
      const dur = (np && np.duration_ms ? (np.duration_ms / 1000) : (audio.duration || 0)) || 0;
      const seekable = (np && np.seekable_ms ? (np.seekable_ms / 1000) : dur) || dur;
      if (!isFinite(dur) || dur <= 0) return;
      const step = e.shiftKey ? 10 : 5;
      if (e.key === "ArrowLeft") { audio.currentTime = Math.max(0, audio.currentTime - step); updateUI(); e.preventDefault(); }
      if (e.key === "ArrowRight") { audio.currentTime = Math.min(seekable || dur, audio.currentTime + step); updateUI(); e.preventDefault(); }
      if (e.key === "Home") { audio.currentTime = 0; updateUI(); e.preventDefault(); }
      if (e.key === "End") { audio.currentTime = seekable || dur; updateUI(); e.preventDefault(); }
    });
  }

  audio.addEventListener("timeupdate", updateUI);
  audio.addEventListener("loadedmetadata", updateUI);
  audio.addEventListener("durationchange", updateUI);
  audio.addEventListener("seeked", updateUI);
  audio.addEventListener("play", updateUI);
  audio.addEventListener("pause", updateUI);

  audio.addEventListener("loadstart", () => setBuffering(true));
  audio.addEventListener("waiting", () => setBuffering(true));
  audio.addEventListener("stalled", () => setBuffering(true));
  audio.addEventListener("canplay", () => setBuffering(false));
  audio.addEventListener("playing", () => setBuffering(false));
  audio.addEventListener("error", () => setBuffering(false));

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
    if (v <= 0.001) icon.textContent = "🔇";
    else if (v < 0.5) icon.textContent = "🔉";
    else icon.textContent = "🔊";
  }

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
      if (v <= 0.001) setVol(gs.__lastNonZeroVol || 1);
      else { gs.__lastNonZeroVol = v; setVol(0); }
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

  if (bReplay) bReplay.innerHTML = icons.replay();
  if (bLike) bLike.innerHTML = icons.thumbUp(false);
  if (bDislike) bDislike.innerHTML = icons.thumbDown(false);

  if (bReplay && !bReplay.__helixBound) {
    bReplay.__helixBound = true;
    bReplay.addEventListener("click", async () => {
      const audio = getOrCreateAudio();
      if (!audio) return;
      audio.currentTime = 0;
      setUserPaused(false);
      const gs = getGlobalState();
      gs.__isFirstLoad = false;
      safePlay(audio);
    });
  }

  if (bPrev && !bPrev.__helixBound) {
    bPrev.__helixBound = true;
    bPrev.addEventListener("click", async () => {
      try {
          setUserPaused(false);
          getGlobalState().__isFirstLoad = false;
        const st = await backend.playerState();
        const audio = getOrCreateAudio();

        if (st && st.active_station_id && audio) {
          audio.currentTime = 0;
          if (!audio.paused) audio.play().catch(() => {});
          return;
        }

        await backend.playerPrev();
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {}
    });
  }

  if (bNext && !bNext.__helixBound) {
    bNext.__helixBound = true;
    bNext.addEventListener("click", async () => {
      try {
        setUserPaused(false);
        getGlobalState().__isFirstLoad = false;
        await backend.playerNext();
        await syncOnce(true);
      } catch {}
    });
  }

  if (bPlay && !bPlay.__helixBound) {
    bPlay.__helixBound = true;
    bPlay.addEventListener("click", async () => {
      const audio = getOrCreateAudio();
      if (!audio) return;
      if (!audio.paused) {
        audio.pause();
        setUserPaused(true);
      } else {
        setUserPaused(false);
        const gs = getGlobalState();
        gs.__isFirstLoad = false; // user gesture unlocks autoplay for subsequent actions
        safePlay(audio);
      }
      try { bPlay.textContent = audio.paused ? "▶" : "⏸"; } catch {}
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
        bLike.innerHTML = icons.thumbUp(!!(res && res.liked));
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

        bDislike.innerHTML = icons.thumbDown(!!(res && res.disliked));
        if (res && res.disliked) {
          setUserPaused(false);
          markUserInitiatedPlayback();
          try { await backend.playerNext(); } catch {}
          await syncOnce(true);
        } else {
          await syncOnce(false);
        }
      } catch {}
    });
  }
}

function nowPlayingKey(np) {
  if (!np) return null;
  return (
    (np.subsonic_song_id ? String(np.subsonic_song_id) : "") ||
    (np.yt_video_id ? String(np.yt_video_id) : "") ||
    ((np.title || "") + "|||" + (np.artist || "") + "|||" + (np.album || ""))
  );
}

function updatePlayerBar(st) {
  const np = st.now_playing;
  if (!np) return;

  const gs = getGlobalState();
  const wKey = nowPlayingKey(np) || np.id;
  if (wKey && wKey !== gs.__waveKey) {
    gs.__waveKey = wKey;
    gs.__waveValues = makeWaveformValues(String(wKey), 120);
  }

  setText("pbTitle", np.title);
  setText("pbSub", np.artist);
  const gs2 = getGlobalState();
  gs2.__pbMetaBase = (np.album ? np.album : "");
  setText("pbMeta", gs2.__isBuffering ? (gs2.__pbMetaBase ? `${gs2.__pbMetaBase} • Loading…` : "Loading…") : gs2.__pbMetaBase);

// Cover art in playbar: mirror Now Playing cover. Prefer backend art_url, else fall back to pqNowImg if present.
const pbThumbEl = document.getElementById("pbThumb");
if (pbThumbEl) {
  const pqImg = document.getElementById("pqNowImg");
  const src = (np.art_url || (pqImg && pqImg.getAttribute("src")) || "");
  if (src) {
    pbThumbEl.setAttribute("src", src);
  } else if (!pbThumbEl.getAttribute("src")) {
    // Leave as-is if already set; otherwise clear.
    pbThumbEl.removeAttribute("src");
  }
}


  // play/pause icon should reflect LOCAL audio element state (Chromium-friendly).
  const bPlay = qs("pbPlay");
  const audioEl = (getGlobalState().audio || document.getElementById("helix-audio"));
  const isPlayingLocal = audioEl ? !audioEl.paused : false;
  if (bPlay) bPlay.textContent = isPlayingLocal ? "⏸" : "▶";

  // like/dislike icons for current item
  const likeBtn = document.getElementById("pbLike");
  const dislikeBtn = document.getElementById("pbDislike");
  const gs3 = getGlobalState();
  const likeKey = (np.subsonic_song_id ? `subsonic:${np.subsonic_song_id}` : (np.yt_video_id ? `yt:${np.yt_video_id}` : ""));

  if (likeBtn && likeKey && gs3.__lastLikeKey !== likeKey) {
    gs3.__lastLikeKey = likeKey;
    likeBtn.innerHTML = icons.thumbUp(false);
    backend.likesIsLiked({ yt_video_id: np.yt_video_id || null, subsonic_song_id: np.subsonic_song_id || null })
      .then((r) => { likeBtn.innerHTML = icons.thumbUp(!!(r && r.liked)); })
      .catch(() => { likeBtn.innerHTML = icons.thumbUp(false); });
  }

  if (dislikeBtn && likeKey && gs3.__lastDislikeKey !== likeKey) {
    gs3.__lastDislikeKey = likeKey;
    dislikeBtn.innerHTML = icons.thumbDown(false);
    backend.dislikesIsDisliked({ yt_video_id: np.yt_video_id || null, subsonic_song_id: np.subsonic_song_id || null })
      .then((r) => { dislikeBtn.innerHTML = icons.thumbDown(!!(r && r.disliked)); })
      .catch(() => { dislikeBtn.innerHTML = icons.thumbDown(false); });
  }

  // Station mode header (Now Playing page)
  const modeWrap = document.getElementById("npMode");
  const modeWrapV2 = document.getElementById("npNowStation");
  const active = st.active_station && st.active_station_id;

  function applyStationUI(wrapEl, idsPrefix) {
    if (!wrapEl) return;
    if (active) {
      wrapEl.style.display = "block";
      const s = st.active_station;
      const nameEl = document.getElementById(idsPrefix + "Name");
      const seedEl = document.getElementById(idsPrefix + "Seed");
      const metaEl = document.getElementById(idsPrefix + "Meta");
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
      wrapEl.style.display = "none";
    }
  }

  applyStationUI(modeWrap, "npStation");
  applyStationUI(modeWrapV2, "npNowStation");
}

async function syncOnce(forceLoadStream) {
  const gs = getGlobalState();
  const st = await backend.playerState();

  // Cache latest good state for fast restore on hard refresh.
  try { localStorage.setItem("helix_last_player_state", JSON.stringify(st)); } catch {}

  gs.__lastStatus = st;
  gs.__lastNowPlaying = st && st.now_playing ? st.now_playing : null;

  bindButtons();

  const np = st.now_playing;
  if (!np) return;

  updatePlayerBar(st);

  try {
    document.dispatchEvent(new CustomEvent("helix-player-state", { detail: { status: st, now_playing: np } }));
  } catch {}

  const audio = getOrCreateAudio();
  if (!audio) return;

  ensureProgressBindings(audio);
  ensureVolumeBindings(audio);

  if (!audio.__helixEndedBound) {
    audio.__helixEndedBound = true;
    audio.addEventListener("ended", async () => {
      try {
        const posMs = Number.isFinite(audio.currentTime) ? Math.floor(audio.currentTime * 1000) : null;
        await backend.playerEnded(posMs);
        await syncOnceWithRetry(true);
      } catch {}
    });
  }

  const curNowKey = nowPlayingKey(np) || (np && np.id ? String(np.id) : null);
  const shouldLoad = forceLoadStream || (curNowKey && curNowKey !== gs.lastNowKey);
  gs.lastNowKey = curNowKey;

  if (shouldLoad) {
    audio.src = backend.playerStreamUrl(np.id);
    try { audio.load(); } catch {}
    // Autoplay unless user explicitly paused.
    // - On the very first page load in a tab, we do NOT autoplay.
    // - HOWEVER, if the user initiated playback (e.g., clicked Play Album/Station) we SHOULD autoplay once the stream is ready.
    const userPaused = getUserPaused();
    const allow = !userPaused && (!gs.__isFirstLoad || gs.__allowAutoplayOnce);
    if (allow) {
      safePlay(audio);
      gs.__allowAutoplayOnce = false;
      gs.__isFirstLoad = false;
    }

    // One-shot refresh shortly after starting a new stream (for lazy metadata/art updates).
    if (gs.postRefreshTimer) {
      clearTimeout(gs.postRefreshTimer);
      gs.postRefreshTimer = null;
    }
    gs.postRefreshForId = np.id;
    gs.postRefreshTimer = setTimeout(() => {
      try {
        const curId = getGlobalState().lastNowKey;
        if (curId && curId === getGlobalState().postRefreshForId) {
          // Refresh metadata/art. IMPORTANT: do NOT force play/pause based on backend state.
          syncOnce(false).catch(() => {});
        }
      } catch {}
    }, 250);
  }

  // If we already have a stream loaded, ensure we stay playing unless the user paused (except first load).
  if (!gs.__isFirstLoad && !getUserPaused()) {
    try { if (audio && audio.paused) safePlay(audio); } catch {}
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

export function getPlayerSnapshot() {
  const gs = getGlobalState();
  return {
    status: gs.__lastStatus || null,
    now_playing: gs.__lastNowPlaying || null,
  };
}

export function startPlayerPolling() {
  const gs = getGlobalState();

  // First page load in this tab should not autoplay.
  const isFirstLoad = !sessionStorage.getItem(FIRST_LOAD_KEY);
  try { sessionStorage.setItem(FIRST_LOAD_KEY, "1"); } catch {}
  gs.__isFirstLoad = isFirstLoad;

  syncOnceWithRetry(false).catch(() => {});

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

  if (!gs.__refreshEventBound) {
    gs.__refreshEventBound = true;
    document.addEventListener("helix-player-refresh", async (ev) => {
      const force = !!(ev && ev.detail && ev.detail.forceLoadStream);
      // This event is fired by user actions (Play Track/Album/Playlist/Station).
      // Queue changes can take time to materialize on the backend (state/now_playing may lag).
      // We wait briefly for now_playing to change, then load+autoplay the new stream.
      if (force) {
        markUserInitiatedPlayback();
        const prev = getGlobalState().lastNowKey;
        const st = await waitForNowPlayingChange(prev, 20000);
        // If we observed a change, load it immediately. Otherwise fall back to a normal sync.
        if (st && st.now_playing) {
          // Prime globals so syncOnce will see the new item.
          getGlobalState().__lastStatus = st;
        }
        syncOnce(true).catch(() => {});
        return;
      }
      syncOnce(false).catch(() => {});
    });  }
}
