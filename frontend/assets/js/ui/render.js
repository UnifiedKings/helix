import { el } from "./dom.js";
import { esc, fmtMs } from "../utils/text.js";
import { coverUrlForRelease, coverUrlForReleaseGroup, activateImage } from "../api/coverart.js";

export function renderResults(state, onSelect) {
  el.results.innerHTML = "";

  if (!state.lastResults.length) {
    el.results.innerHTML = `<div class="item"><div>No results.</div><div class="muted">Try another search.</div></div>`;
    el.count.textContent = "";
    el.page.textContent = "";
    el.prevBtn.disabled = true;
    el.nextBtn.disabled = true;
    return;
  }

  state.lastResults.forEach((r) => {
    const div = document.createElement("div");
    div.className = "item" + (r._id === state.activeId ? " active" : "");
    div.onclick = () => onSelect(r._id);

    if (state.type === "recording") {
      const artist = (r["artist-credit"]?.[0]?.name) || (r.artist?.name) || "Unknown artist";
      const release = r._repReleaseTitle || r.releases?.[0]?.title || "";
      const releaseId = r._repReleaseId || r.releases?.[0]?.id || "";
      const dur = fmtMs(r.length);

      div.innerHTML = `
        <div class="resultRow">
          <img class="thumb" alt="" loading="lazy">
          <div>
            <div><strong>${esc(r.title)}</strong> — ${esc(artist)}</div>
            <div class="muted">${release ? esc(release) + " · " : ""}${dur ? dur + " · " : ""}${esc(r.id)}</div>
          </div>
        </div>
      `;

      const img = div.querySelector("img.thumb");
      if (img && releaseId) {
        // If the user chooses to hide tracks without art, remove items whose thumbnail 404s.
        if (state.settings?.search_hide_tracks_without_art) {
          img.onload = () => img.classList.add("loaded");
          img.onerror = () => div.remove();
          img.style.visibility = "visible";
          img.src = coverUrlForRelease(releaseId, 250);
        } else {
          activateImage(img, coverUrlForRelease(releaseId, 250));
        }
      } else if (img) img.style.visibility = "hidden";
    } else if (state.type === "release-group") {
      const artist = (r["artist-credit"]?.[0]?.name) || "Unknown artist";
      const date = r["first-release-date"] || "";
      const primaryType = r["primary-type"] || "";

      div.innerHTML = `
        <div class="resultRow">
          <img class="thumb" alt="" loading="lazy">
          <div>
            <div><strong>${esc(r.title)}</strong> — ${esc(artist)}</div>
            <div class="muted">${primaryType ? esc(primaryType) + " · " : ""}${date ? esc(date) + " · " : ""}${esc(r.id)}</div>
          </div>
        </div>
      `;

      const img = div.querySelector("img.thumb");
      if (img) activateImage(img, coverUrlForReleaseGroup(r.id, 250));

    } else {
      const artist = (r["artist-credit"]?.[0]?.name) || "Unknown artist";
      const date = r.date || "";
      const country = r.country || "";

      div.innerHTML = `
        <div class="resultRow">
          <img class="thumb" alt="" loading="lazy">
          <div>
            <div><strong>${esc(r.title)}</strong> — ${esc(artist)}</div>
            <div class="muted">${date ? esc(date) + " · " : ""}${country ? esc(country) + " · " : ""}${esc(r.id)}</div>
          </div>
        </div>
      `;

      const img = div.querySelector("img.thumb");
      if (img) activateImage(img, coverUrlForRelease(r.id, 250));
    }

    el.results.appendChild(div);
  });

  const pageNum = Math.floor(state.offset / state.limit) + 1;
  const totalPages = Math.max(1, Math.ceil(state.lastCount / state.limit));
  el.count.textContent = `${state.lastCount.toLocaleString()} total`;
  el.page.textContent = `Page ${pageNum} / ${totalPages}`;

  el.prevBtn.disabled = state.offset === 0;
  el.nextBtn.disabled = (state.offset + state.limit) >= state.lastCount;
}

export function renderDetails(rows, links) {
  el.details.innerHTML = rows.map(([k, v]) => `
    <div class="k">${esc(k)}</div><div class="v">${v}</div>
  `).join("");

  el.links.innerHTML = (links || [])
    .map(l => `<a href="${l.href}" target="_blank" rel="noopener">${esc(l.text)}</a>`)
    .join("");
}
