// Lightweight SPA-style navigation (PJAX) to keep the audio player alive across pages.
//
// Goal: audio keeps playing when the user navigates to different "pages" in Helix.
// We do this by preventing full page reloads and swapping only the <main> content.

const PAGE_MODULE_BY_FILE = {
  "index.html": "./pages/now-playing.js",
  "browse.html": "./pages/browse.js",
  "my-collection.html": "./pages/my-collection.js",
  "artist.html": "./pages/artist.js",
  "album.html": "./pages/album.js",
  "search.html": "./app.js",
  "admin.html": "./admin.js",
  "listening-history.html": "./pages/listening-history.js",
  "stations.html": "./pages/stations.js",
  "liked-songs.html": "./pages/liked-songs.js"
};

// Top navigation (global search + auth UI) lives outside <main> and persists across PJAX.
// It must be initialized on first load and refreshed after each navigation.
import { initTopNav } from "./ui/topnav.js";

function isPlainLeftClick(ev) {
  return ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey;
}

function isSameOrigin(url) {
  return url.origin === window.location.origin;
}

function isHtmlPagePath(pathname) {
  return pathname.endsWith(".html");
}

function fileFromUrl(url) {
  const p = url.pathname.split("/").pop();
  return p || "index.html";
}

async function loadAndInitPageModule(file) {
  const modulePath = PAGE_MODULE_BY_FILE[file];
  if (!modulePath) return;

  // Cache the imported module, but allow re-running init() on navigation.
  const mod = await import(modulePath);
  if (typeof mod.init === "function") {
    await mod.init();
  }
}

function replaceMainWith(newDoc) {
  const newMain = newDoc.querySelector("main");
  const curMain = document.querySelector("main");
  if (!newMain || !curMain) return;
  curMain.replaceWith(newMain);
}


function replaceSearchHeaderWith(newDoc) {
  // Search page has a header.searchHeader (tabs/status) that lives OUTSIDE <main>.
  // Since we PJAX-swap only <main>, we must also swap this header so search tabs appear,
  // and remove it when leaving search.
  const newHeader = newDoc.querySelector("header.searchHeader");
  const curHeader = document.querySelector("header.searchHeader");

  if (newHeader) {
    if (curHeader) {
      curHeader.replaceWith(newHeader);
    } else {
      // Insert right after the topbar for consistent layout.
      const topbar = document.querySelector(".topbar");
      if (topbar && topbar.parentNode) {
        topbar.parentNode.insertBefore(newHeader, topbar.nextSibling);
      } else {
        document.body.insertBefore(newHeader, document.body.firstChild);
      }
    }
  } else if (curHeader) {
    curHeader.remove();
  }
}


function updateTitle(newDoc) {
  const t = newDoc.querySelector("title");
  if (t && t.textContent) document.title = t.textContent;
}

async function navigateTo(href, { push = true } = {}) {
  const url = new URL(href, window.location.href);
  if (!isSameOrigin(url) || !isHtmlPagePath(url.pathname)) {
    window.location.href = url.toString();
    return;
  }

  // Fetch the destination page.
  const res = await fetch(url.toString(), {
    method: "GET",
    headers: { "X-Helix-PJAX": "1" },
    credentials: "same-origin",
  });
  if (!res.ok) {
    // Fall back to hard navigation on failure.
    window.location.href = url.toString();
    return;
  }

  const html = await res.text();
  const newDoc = new DOMParser().parseFromString(html, "text/html");

  updateTitle(newDoc);
  replaceMainWith(newDoc);
  replaceSearchHeaderWith(newDoc);

  // Clear page-scoped state that must not persist across PJAX navigations.
  document.body.classList.remove("isSearchPage", "searchActive");

  // Update URL.
  if (push) history.pushState({}, "", url.toString());
  else history.replaceState({}, "", url.toString());

  // Let modules know navigation happened (useful for components that listen).
  window.dispatchEvent(new CustomEvent("helix:navigate", { detail: { url: url.toString() } }));

  // Refresh global top nav UI (active tab highlight, whoami/admin link, search box wiring).
  // Safe to call on every navigation; topnav guards one-time bindings internally.
  try { await initTopNav(); } catch (e) { console.warn("topnav init failed", e); }

  // Initialize the destination page.
  await loadAndInitPageModule(fileFromUrl(url));
}

// Expose a safe programmatic navigation helper so page modules can navigate without
// triggering a full page reload (which would kill the audio player).
// Usage: window.helixNavigate('album.html?id=...')
window.helixNavigate = (href, opts) => navigateTo(href, opts);

function installLinkInterceptor() {
  document.addEventListener("click", (ev) => {
    if (!isPlainLeftClick(ev)) return;
    const a = ev.target?.closest?.("a[href]");
    if (!a) return;

    // Respect explicit targets and downloads.
    if (a.target && a.target !== "_self") return;
    if (a.hasAttribute("download")) return;

    const href = a.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) return;

    const url = new URL(href, window.location.href);
    if (!isSameOrigin(url)) return;
    if (!isHtmlPagePath(url.pathname)) return;

    // Intercept internal html navigation.
    ev.preventDefault();
    navigateTo(url.toString(), { push: true }).catch((e) => {
      console.error("PJAX navigation failed; falling back to hard navigation", e);
      window.location.href = url.toString();
    });
  });
}

function installPopStateHandler() {
  window.addEventListener("popstate", () => {
    navigateTo(window.location.href, { push: false }).catch((e) => {
      console.error("PJAX popstate navigation failed; falling back", e);
      window.location.reload();
    });
  });
}

// Boot router and initialize current page module.
installLinkInterceptor();
installPopStateHandler();

// Initialize global top nav (search box + auth UI) on first load.
initTopNav().catch((e) => console.warn("topnav init failed", e));

// Initialize the global top navigation once on initial load.
initTopNav().catch((e) => console.warn("topnav init failed", e));

// Ensure current page module has a callable init() for consistency.
// (If the page script tag also ran, init() should be idempotent.)
loadAndInitPageModule(fileFromUrl(new URL(window.location.href))).catch(console.error);

// Ensure topnav is wired on hard refresh (e.g., landing directly on admin.html).
initTopNav().catch(console.warn);
