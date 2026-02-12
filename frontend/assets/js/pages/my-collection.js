import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";

function svgPlaceholder(label = "") {
  const safe = label.replace(/[<>&]/g, "");
  const svg = `
  <svg xmlns="http://www.w3.org/2000/svg" width="600" height="600">
    <rect width="100%" height="100%" fill="#0b0f14"/>
    <rect x="16" y="16" width="568" height="568" rx="24" fill="#0f1217" stroke="#232833"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#aab2c0" font-family="system-ui, -apple-system, Segoe UI, Roboto, Arial" font-size="26">${safe}</text>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function setImg(img, src) {
  img.src = src;
  img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
}

function renderRecentlyPlayed(el) {
  const items = [
    { label: "The Shins", art: svgPlaceholder("The Shins") },
    { label: "Broken Bells", art: svgPlaceholder("Broken Bells") },
    { label: "Fleet Foxes", art: svgPlaceholder("Fleet Foxes") },
    { label: "Blind Pilot", art: svgPlaceholder("Blind Pilot") },
    { label: "Tyler Childers", art: svgPlaceholder("Tyler Childers") },
    { label: "Julia Stone", art: svgPlaceholder("Julia Stone") },
  ];
  el.innerHTML = "";
  for (const it of items) {
    const img = document.createElement("img");
    img.className = "tileLg";
    img.alt = it.label;
    img.loading = "lazy";
    setImg(img, it.art);
    el.appendChild(img);
  }
}

function renderStations(el) {
  const stations = [
    { name: "The Shins Radio", desc: "Based on your listening" },
    { name: "Indie Chill", desc: "Similar artists & deep cuts" },
    { name: "Road Trip", desc: "Upbeat mix" },
  ];
  el.innerHTML = "";
  for (const st of stations) {
    const card = document.createElement("div");
    card.className = "stationCard";
    card.innerHTML = `
      <div class="stationIcon" aria-hidden="true">📻</div>
      <div>
        <div class="stationName">${st.name}</div>
        <div class="muted">${st.desc}</div>
      </div>
    `;
    el.appendChild(card);
  }
}

function renderCollected(el) {
  const cards = [
    { title: "Who Laughs Last", sub: "Lord Huron", art: svgPlaceholder("Lord Huron") },
    { title: "Helplessness Blues", sub: "Fleet Foxes", art: svgPlaceholder("Fleet Foxes") },
    { title: "Poor Boy", sub: "Blind Pilot", art: svgPlaceholder("Blind Pilot") },
    { title: "Appaloosa Bones", sub: "Gregory Alan Isakov", art: svgPlaceholder("Isakov") },
    { title: "Ace Up My Sleeve", sub: "Lord Huron", art: svgPlaceholder("Lord Huron") },
    { title: "Whitehouse Road", sub: "Tyler Childers", art: svgPlaceholder("Childers") },
  ];
  el.innerHTML = "";
  for (const c of cards) {
    const wrap = document.createElement("div");
    wrap.className = "miniCard";
    const img = document.createElement("img");
    img.alt = c.title;
    img.loading = "lazy";
    setImg(img, c.art);
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = c.title;
    const s = document.createElement("div");
    s.className = "s";
    s.textContent = c.sub;
    wrap.appendChild(img);
    wrap.appendChild(t);
    wrap.appendChild(s);
    el.appendChild(wrap);
  }
}

export async function init() {
  await initTopNav();

  startPlayerPolling();
// Player bar scaffolding (shared)
  const pbThumb = document.getElementById("pbThumb");
  if (pbThumb) setImg(pbThumb, svgPlaceholder("Helix"));

  const recently = document.getElementById("recentlyPlayed");
  if (recently) renderRecentlyPlayed(recently);

  const stations = document.getElementById("stations");
  if (stations) renderStations(stations);

  const collected = document.getElementById("collected");
  if (collected) renderCollected(collected);
}
