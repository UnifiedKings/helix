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

function renderArtists(el) {
  const artists = [
    "Shook Twins",
    "Junip",
    "Jelly Roll",
    "Palace",
    "Delicate Steve",
    "The Sweepings",
    "Angus Stone",
    "Hunter Metts",
  ];
  el.innerHTML = "";
  for (const name of artists) {
    const card = document.createElement("div");
    card.className = "artist";
    const img = document.createElement("img");
    img.alt = name;
    img.loading = "lazy";
    setImg(img, svgPlaceholder(name));
    const label = document.createElement("div");
    label.className = "name";
    label.textContent = name;
    card.appendChild(img);
    card.appendChild(label);
    el.appendChild(card);
  }
}

function renderNewMusic(el) {
  const items = [
    { title: "Try To Wait", artist: "Jasmine Thompson", meta: "Single — Feb 01, 2026" },
    { title: "Baby Please", artist: "Fredo Bang", meta: "Single — Feb 06, 2026" },
    { title: "OCTANE", artist: "Don Toliver", meta: "Album — 18 songs" },
    { title: "Wake Up Calling", artist: "Papa Roach", meta: "Single — Jan 28, 2026" },
    { title: "Somebody Tried To Sell Me…", artist: "Van Morrison", meta: "Album — 20 songs" },
    { title: "I'm Good (From The Movie…)", artist: "Jelly Roll", meta: "Single — Jan 23, 2026" },
    { title: "Big Ole Fancy House", artist: "Parker McCollum", meta: "Album — Jan 23, 2026" },
    { title: "Covers, Pt.4", artist: "City And Colour", meta: "Album — Jan 23, 2026" },
  ];
  el.innerHTML = "";
  for (const it of items) {
    const card = document.createElement("div");
    card.className = "albumCard";
    const img = document.createElement("img");
    img.alt = it.title;
    img.loading = "lazy";
    setImg(img, svgPlaceholder(it.title));
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = it.title;
    const s = document.createElement("div");
    s.className = "s";
    s.textContent = `${it.artist} — ${it.meta}`;
    card.appendChild(img);
    card.appendChild(t);
    card.appendChild(s);
    el.appendChild(card);
  }
}

function renderGenres(el) {
  const genres = ["Indie", "Rock", "Pop", "Hip-Hop", "Country", "Electronic", "Acoustic", "Chill"]; 
  el.innerHTML = "";
  for (const g of genres) {
    const chip = document.createElement("div");
    chip.className = "genreChip";
    chip.textContent = g;
    el.appendChild(chip);
  }
}

export async function init() {
  await initTopNav();

  startPlayerPolling();
const pbThumb = document.getElementById("pbThumb");
  if (pbThumb) setImg(pbThumb, svgPlaceholder("Helix"));

  const artists = document.getElementById("recommendedArtists");
  if (artists) renderArtists(artists);

  const newMusic = document.getElementById("newMusic");
  if (newMusic) renderNewMusic(newMusic);

  const genres = document.getElementById("genres");
  if (genres) renderGenres(genres);
}
