import {esc} from "../utils/text.js"


function getActiveStationId(st){
  if (!st) return null;
  // Only treat station mode as active when backend sets an active_station_id.
  // Do NOT fall back to st.active_station/st.station objects, as those can be
  // stale remnants from a previous station session.
  return st.active_station_id ? st.active_station_id : null;
}

function getActiveStationName(st){
  if (!st) return null;
  if (st.active_station_name) return st.active_station_name;
  const a = st.active_station || st.station || null;
  return (a && (a.name || a.title)) ? (a.name || a.title) : null;
}

import * as backend from "../api/backend.js";
import { startPlayerPolling, getPlayerSnapshot } from "../player.js";
import * as icons from "../ui/icons.js";
import { initTopNav } from "../ui/topnav.js";

function $(id) { return document.getElementById(id); }


/**
 * Helper function to add text to an element with a given id
 * @param {str} id id of the element being changed
 * @param {str} text text being placed into element with {id}
 * @returns 
 */
function setText(id, text) 
{
  const el = $(id);
  if (!el) return;
  el.textContent = (text ?? "") + "";
}
/**
 * Helper function to set the image of an element
 * @param {str} id id of the element being updated
 * @param {str} url url of the image.
 * @returns 
 */
function setImg(id, url) {
  const el = $(id);
  if (!el) return;
  if (!url) { el.removeAttribute("src"); return; }
  el.setAttribute("src", url);
}

function fmtDur(msOrSec){
  const n = (msOrSec == null) ? 0 : Number(msOrSec);
  if (!isFinite(n) || n <= 0) return "";
  // some fields are seconds even if named *_ms
  const totalSec = n > 100000 ? Math.round(n / 1000) : Math.round(n);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2,'0')}`;
}

/**
 * Gets the key for what is playing
 * @param {*} now_playing 
 * @returns 
 */
function nowKey(now_playing) {
  if (!now_playing) return null;
  var output = (
    (now_playing.subsonic_song_id ? String(now_playing.subsonic_song_id) : "") ||
    (now_playing.yt_video_id ? String(now_playing.yt_video_id) : "") ||
    ((now_playing.title || "") + "|||" + (now_playing.artist || "") + "|||" + (now_playing.album || ""))
  );
  return output
}

/**
 * Renders the queue of songs for the now playing page.
 * @param {*} queue The queue of songs being rendered
 * @param {*} currentIndex the index of the song that is "now playing"
 * @returns 
 */
async function renderQueue(queue, currentIndex) {
  const list = $("npQueueList");
  if (!list) return;

  list.innerHTML = "";
  if (!Array.isArray(queue)) return;

  for (let i = 0; i < queue.length; i++) {
    const q = queue[i];
    const row = document.createElement("div");
    row.className = "npQueueRow" + (i === currentIndex ? " active" : "");
    row.innerHTML = `
      <div class="npQueueTitle">${(q.title || "")}</div>
      <div class="npQueueSub muted">${(q.artist || "")}${q.album ? " — " + q.album : ""}</div>
    `;
    row.addEventListener("click", async () => {
      try {
        await backend.playerJump(i);
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {}
    });
    list.appendChild(row);
  }
}

async function renderQueueDetailed(queue, currentIndex){
  const list = document.getElementById("pqList");
  if (!list) return;
  list.innerHTML = "";
  const items = Array.isArray(queue) ? queue : [];

  // header hint
  const sub = document.getElementById("pqSub");
  if (sub) sub.textContent = `${items.length} songs`;

  for (let i = 0; i < items.length; i++){
    const q = items[i] || {};
    const row = document.createElement("div");
    row.className = "pqRow" + (i === currentIndex ? " active" : "");
    const art = q.art_url || "";
    row.innerHTML = `
      <div class="pqIdx">${i+1}</div>
      <img class="pqThumb" alt="" ${art ? `src="${esc(art)}"` : ""} />
      <div class="pqText">
        <div class="pqTrackTitle">${esc(q.title || "—")}</div>
        <div class="pqTrackSub">${esc(q.artist || "—")}</div>
      </div>
      <div class="pqDur">${esc(fmtDur(q.duration_ms || q.duration || 0))}</div>
    `;
    row.addEventListener("click", async () => {
      try {
        await backend.playerJump(i);
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {}
    });
    list.appendChild(row);
  }
}
/**
 * Helper function to return a string representing when something was played into the recently played history.
 * @param {*} iso 
 * @returns 
 */
function fmtWhen(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso || ""; }
}


/**
 * Renders the history of songs that played on a specific station
 * @param {*} items The history of the station being rendered
 * @returns 
 */
function renderHistoryInto(list, items) {
  if (!list) return;
  list.innerHTML = "";
  const max = Array.isArray(items) ? items.slice(0, 200) : [];
  const likeSeqByHistoryId = new Map();

  for (const h of max) {
    const row = document.createElement("div");
    row.className = "npHistRow";

    const img = document.createElement("img");
    img.className = "npHistImg";
    img.alt = "";
    if (h.art_url) img.src = h.art_url;

    const text = document.createElement("div");
    text.className = "npHistText";
    text.innerHTML = `
      <div class="npHistTitle">${esc(h.title || "—")}</div>
      <div class="npHistSub muted">${esc(h.artist || "—")}${h.album ? " — " + esc(h.album) : ""}</div>
      <div class="npHistSub muted">${esc(h.played_at || h.ts || "")}</div>
    `;

    const actions = document.createElement("div");
    actions.className = "npHistActions";

    const likeBtn = document.createElement("button");
    likeBtn.className = "iconBtn npLikeBtn";
    likeBtn.type = "button";
    likeBtn.setAttribute("aria-label", "Thumb up");
    likeBtn.innerHTML = icons.thumbUp(false);

    const replayBtn = document.createElement("button");
    replayBtn.className = "iconBtn";
    replayBtn.type = "button";
    replayBtn.setAttribute("aria-label", "Replay");
    replayBtn.innerHTML = icons.replay();

    actions.appendChild(likeBtn);
    actions.appendChild(replayBtn);

    row.appendChild(img);
    row.appendChild(text);
    row.appendChild(actions);
    list.appendChild(row);

    // Like state
    (async () => {
      const seq = (likeSeqByHistoryId.get(h.id) || 0) + 1;
      likeSeqByHistoryId.set(h.id, seq);
      try {
        const likedRes = await backend.likesIsLiked({ subsonic_song_id: h.subsonic_song_id || null, yt_video_id: h.yt_video_id || null });
        if (likeSeqByHistoryId.get(h.id) !== seq) return;
        const liked = !!(likedRes && (likedRes.liked ?? likedRes.is_liked ?? likedRes.isLiked ?? likedRes === true));
        likeBtn.innerHTML = icons.thumbUp(liked);
        likeBtn.dataset.liked = liked ? "1" : "0";
      } catch {}
    })();

    likeBtn.addEventListener("click", async () => {
      const isLikedNow = likeBtn.dataset.liked === "1";
      likeBtn.innerHTML = icons.thumbUp(!isLikedNow);
      likeBtn.dataset.liked = (!isLikedNow) ? "1" : "0";
      try {
        await backend.likesToggle({
          title: h.title,
          artist: h.artist,
          album: h.album || "",
          duration_ms: h.duration_ms || 0,
          art_url: h.art_url || "",
          source: h.source || "",
          subsonic_song_id: h.subsonic_song_id || null,
          yt_video_id: h.yt_video_id || null,
        });
      } catch (e) {
        likeBtn.innerHTML = icons.thumbUp(isLikedNow);
        likeBtn.dataset.liked = isLikedNow ? "1" : "0";
      }
    });

    replayBtn.addEventListener("click", async () => {
      try {
        await backend.playerReplayFromHistory(h.id);
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {}
    });
  }
}

async function renderHistory(items) {
  const list = $("npHistoryList");
  renderHistoryInto(list, Array.isArray(items) ? items.slice(0,30) : []);
  return;

  /* legacy inline renderer removed */
  const max = []; for (const h of max) {
    const row = document.createElement("div");
    row.className = "npHistRow";

    const img = document.createElement("img");
    img.className = "npHistImg";
    img.alt = "";
    if (h.art_url) img.src = h.art_url;

    const text = document.createElement("div");
    text.className = "npHistText";
    text.innerHTML = `
      <div class="npHistTitle">${h.title || ""}</div>
      <div class="npHistSub muted">${h.artist || ""}${h.album ? " — " + h.album : ""}</div>
      <div class="npHistWhen muted">${fmtWhen(h.created_at)}</div>
    `;

    const actions = document.createElement("div");
    actions.className = "npHistActions";

    const likeBtn = document.createElement("button");
    likeBtn.className = "iconBtn npLikeBtn";
    likeBtn.type = "button";
    likeBtn.innerHTML = icons.thumbUp(false);

    const playBtn = document.createElement("button");
    playBtn.className = "iconBtn";
    playBtn.type = "button";
    playBtn.innerHTML = icons.replay();

    actions.appendChild(likeBtn);
    actions.appendChild(playBtn);

    row.appendChild(img);
    row.appendChild(text);
    row.appendChild(actions);
    list.appendChild(row);

    // async liked state (avoid flicker / stale updates)
    const seq = (likeSeqByHistoryId.get(h.id) || 0) + 1;
    likeSeqByHistoryId.set(h.id, seq);

    (async () => {
      try {
        const likedRes = await backend.likesIsLiked({ subsonic_song_id: h.subsonic_song_id || null, yt_video_id: h.yt_video_id || null });
        if (likeSeqByHistoryId.get(h.id) !== seq) return;
        const liked = !!(likedRes && (likedRes.liked ?? likedRes.is_liked ?? likedRes.isLiked ?? likedRes === true));
        likeBtn.innerHTML = icons.thumbUp(liked);
        likeBtn.dataset.liked = liked ? "1" : "0";
      } catch {}
    })();

    likeBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const curSeq = (likeSeqByHistoryId.get(h.id) || 0) + 1;
      likeSeqByHistoryId.set(h.id, curSeq);

      const isLikedNow = likeBtn.dataset.liked === "1";
      likeBtn.innerHTML = icons.thumbUp(!isLikedNow);
      likeBtn.dataset.liked = (!isLikedNow) ? "1" : "0";

      try {
        await backend.likesToggle({
        title: h.title,
        artist: h.artist,
        album: h.album || "",
        duration_ms: h.duration_ms || 0,
        art_url: h.art_url || "",
        source: h.source || "",
        subsonic_song_id: h.subsonic_song_id || null,
        yt_video_id: h.yt_video_id || null,
      });
      } catch {
        // revert on failure
        if (likeSeqByHistoryId.get(h.id) === curSeq) {
          likeBtn.innerHTML = icons.thumbUp(isLikedNow);
          likeBtn.dataset.liked = isLikedNow ? "1" : "0";
        }
      }
    });

    playBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      playBtn.disabled = true;
      try {
        const posMs = h.position_ms ? Math.max(0, parseInt(h.position_ms, 10) || 0) : 0;
        await backend.playerReplayFromHistory(h.id, posMs);
        document.dispatchEvent(new CustomEvent("helix-player-refresh", { detail: { forceLoadStream: true } }));
      } catch {} finally {
        playBtn.disabled = false;
      }
    });
  }
}

function renderStationUI(st) {
  if (!st) return;
  const container = document.getElementById("npNowStation");
  if (!container) return;

  const sid = getActiveStationId(st);
  const stationName = getActiveStationName(st) || "Station";

  container.style.display = sid ? "block" : "none";
  setText("npNowStationName", stationName);
  // Seed/meta are optional depending on backend shape
  if (st.active_station && st.active_station.seed_artist) {
    setText("npNowStationSeed", st.active_station.seed_artist);
  } else {
    setText("npNowStationSeed", "");
  }
  if (st.discovery != null) {
    setText("npNowStationMeta", `Discovery: ${Math.round((st.discovery || 0) * 100)}%`);
  } else {
    setText("npNowStationMeta", "");
  }

  const hb = document.getElementById("npStationHistoryBtn");
  if (hb){ hb.disabled = false; hb.classList.remove("disabled"); }

  // Any additional station meta (optional elements)
  const dEl = $("npDiscoveryPct");
  if (dEl && st.discovery != null) {
    dEl.textContent = `Discovery: ${Math.round((st.discovery || 0) * 100)}%`;
  }
}

async function renderNowPlaying(st) {
  const np = st && st.now_playing ? st.now_playing : null;
  if (!np) return;

  const isStation = !!getActiveStationId(st);
  const stationShell = document.getElementById("npStationShell");
  const queueShell = document.getElementById("npQueueShell");
  if (stationShell) stationShell.style.display = isStation ? "" : "none";
  if (queueShell) queueShell.style.display = isStation ? "none" : "";

  // Always render station UI so it can hide itself when station mode ends.
  renderStationUI(st);

  // v2 now ids
  setText("npNowTitle", np.title);
  setText("npNowArtist", np.artist);
  setImg("npNowImg", np.art_url || "");

  // playlist/queue ids
  setText("pqNowTitle", np.title);
  setText("pqNowArtist", np.artist);
  setImg("pqNowImg", np.art_url || "");

  if (!isStation) {
    // render queue list
    await renderQueueDetailed(st.queue, st.current_index);
  }
}

let __lastHistKey = null;
let __lastHistStation = null;
let __histLoaded = false;

async function maybeRefreshHistory(st) {
  const list = $("npHistoryList");
  if (!list) return;

  const np = st && st.now_playing ? st.now_playing : null;
  const key = nowKey(np);
  const sid = st ? (st.active_station_id || null) : null;

  const should = (!__histLoaded) || (__lastHistKey !== key) || (__lastHistStation !== sid);
  if (!should) return;

  const hist = await backend.getListeningHistory(sid);
  await renderHistory(hist && hist.items ? hist.items : []);
  __histLoaded = true;
  __lastHistKey = key;
  __lastHistStation = sid;
}

function wireAutoplayToggle(st) {
  const ap = $("autoplayToggle");
  const apTitle = document.querySelector(".npToggleTitle");
  if (!ap) return;

  ap.checked = !!st.autoplay_enabled;
  if (apTitle) apTitle.textContent = st.autoplay_enabled ? "Autoplay is on" : "Autoplay is off";

  if (ap.__helixWired) return;
  ap.__helixWired = true;

  ap.addEventListener("change", async () => {
    try {
      await backend.playerSetAutoplay(ap.checked);
    } catch {}
  });
}

function onPlayerState(ev) {
  const st = ev?.detail?.status;
  if (!st) return;

  renderNowPlaying(st).catch(() => {});
  maybeRefreshHistory(st).catch(() => {});
  wireAutoplayToggle(st);
}

export async function init() {
  await initTopNav();

  

  // Station play-history modal wiring
  const histBtn = document.getElementById("npStationHistoryBtn");
  const histModal = document.getElementById("npStationHistoryModal");
  const histClose = document.getElementById("npStationHistoryClose");
  const histList = document.getElementById("npStationHistoryList");
  const histSub = document.getElementById("npStationHistorySub");

  function closeHist(){
    if (histModal) histModal.style.display = "none";
  }
  function openHist(){
    if (histModal) histModal.style.display = "flex";
  }
  histClose?.addEventListener("click", closeHist);
  histModal?.addEventListener("click", (e) => { if (e.target === histModal) closeHist(); });

  histBtn?.addEventListener("click", async () => {
    try{
      const snap = getPlayerSnapshot();
      const st = snap && snap.status ? snap.status : await backend.playerState();
      const sid = getActiveStationId(st);
      if (!sid){
        openHist();
        if (histList) histList.innerHTML = "<div class='muted'>No active station.</div>";
        return;
      }
      if (histSub) histSub.textContent = (getActiveStationName(st) || "Station");
      openHist();
      if (histList) histList.innerHTML = "<div class='muted'>Loading…</div>";
      const hist = await backend.getListeningHistory(sid);
      const items = hist && hist.items ? hist.items : [];
      renderHistoryInto(histList, items);
    } catch(e){
      if (histList) histList.innerHTML = "<div class='muted'>Failed to load history.</div>";
      console.log(e)
    }
  });
if (!window.__helixNowPlayingWired) {
    window.__helixNowPlayingWired = true;
    document.addEventListener("helix-player-state", onPlayerState);
  }

  // Render immediately from last snapshot if available.
  const snap = getPlayerSnapshot();
  if (snap) {
    onPlayerState({ detail: { status: snap.status, now_playing: snap.now_playing } });
  }

  startPlayerPolling();
}
