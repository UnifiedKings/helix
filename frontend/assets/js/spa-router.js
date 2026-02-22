// SPA Router for Helix (single shell, view swapping)
// Keeps topbar + playerBar persistent and avoids duplicated HTML across pages.

import { initTopNav } from "./ui/topnav.js";
import { VIEW_MANIFEST } from "./view-manifest.js";


const TEMPLATE_CACHE = new Map();

async function fetchTemplate(url) {
  if (TEMPLATE_CACHE.has(url)) return TEMPLATE_CACHE.get(url);
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`Failed to load template: ${url} (${res.status})`);
  const text = await res.text();
  TEMPLATE_CACHE.set(url, text);
  return text;
}

function extractHeaderAndMain(fragmentHtml) {
  const wrap = document.createElement("div");
  wrap.innerHTML = fragmentHtml;
  const header = wrap.querySelector("header.searchHeader");
  const main = wrap.querySelector("main");
  return {
    headerHtml: header ? header.outerHTML : "",
    mainHtml: main ? main.outerHTML : "<main class='shell'><div class='muted'>Missing view</div></main>",
  };
}

function isPlainLeftClick(ev) {
  return ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey;
}

function fileFromUrl(url) {
  const p = url.pathname.split("/").pop();
  return (p && p.endsWith(".html")) ? p : "index.html";
}

function setActiveNav(file) {
  document.querySelectorAll(".navLink").forEach(a => {
    const href = a.getAttribute("href") || "";
    const f = href.split("/").pop();
    a.classList.toggle("active", f === file);
  });
}

function replaceHeader(html) {
  const slot = document.getElementById("helixHeaderSlot");
  if (!slot) return;
  slot.innerHTML = html || "";
}

function replaceMain(mainHtml) {
  const curMain = document.querySelector("main");
  if (!curMain) return;

  const tmp = document.createElement("div");
  tmp.innerHTML = mainHtml || "<main class='shell'><div class='muted'>Missing view</div></main>";
  const newMain = tmp.querySelector("main");
  if (!newMain) return;

  curMain.replaceWith(newMain);
}

async function loadAndInit(file) {
  const entry = VIEW_MANIFEST[file] || VIEW_MANIFEST["index.html"];
  if (!entry) throw new Error(`No view manifest entry for ${file}`);

  const html = await fetchTemplate(entry.templateUrl);
  const view = { ...extractHeaderAndMain(html), initModule: entry.initModule };
replaceHeader(view.headerHtml);
  replaceMain(view.mainHtml);
  setActiveNav(file);

  window.dispatchEvent(new CustomEvent("helix:navigate", { detail: { file } }));

  await initTopNav();

  if (view.initModule) {
    const mod = await import(view.initModule);
    if (typeof mod.init === "function") await mod.init();
  }
}

async function navigateTo(href, { push = true } = {}) {
  const url = new URL(href, window.location.href);
  const file = fileFromUrl(url);
  const newUrl = file + url.search;

  if (push) history.pushState({}, "", newUrl);
  else history.replaceState({}, "", newUrl);

  await loadAndInit(file);
}

window.helixNavigate = (href, opts) => navigateTo(href, opts);

function installLinkInterceptor() {
  document.addEventListener("click", (ev) => {
    if (!isPlainLeftClick(ev)) return;
    const a = ev.target?.closest?.("a[href]");
    if (!a) return;
    if (a.target && a.target !== "_self") return;
    if (a.hasAttribute("download")) return;

    const href = a.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) return;

    const url = new URL(href, window.location.href);
    const file = fileFromUrl(url);
    if (!file) return;

    ev.preventDefault();
    navigateTo(file + url.search, { push: true }).catch((e) => {
      console.error("SPA navigation failed; falling back", e);
      window.location.href = href;
    });
  });
}

window.addEventListener("popstate", () => {
  loadAndInit(fileFromUrl(new URL(window.location.href))).catch((e) => {
    console.error("SPA popstate failed", e);
    window.location.reload();
  });
});

installLinkInterceptor();

// If we came from a stub redirect (?to=...), restore intended route once.
const params = new URLSearchParams(window.location.search || "");
const to = params.get("to");
if (to) {
  params.delete("to");
  const rest = params.toString();
  const decoded = decodeURIComponent(to);
  const dest = decoded + (rest ? ((decoded.includes("?") ? "&" : "?") + rest) : "");
  history.replaceState({}, "", "index.html");
  initTopNav().catch(console.warn);
  navigateTo(dest, { push: true }).catch(console.error);
} else {
  initTopNav().catch(console.warn);
  loadAndInit(fileFromUrl(new URL(window.location.href))).catch(console.error);
}
