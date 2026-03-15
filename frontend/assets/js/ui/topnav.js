import * as backend from "../api/backend.js";
import { requireAuth } from "../auth-guard.js";

function setActiveNav() {
  const path = (window.location.pathname || "").split("/").pop() || "index.html";
  const map = {
    "index.html": "now",
    "my-collection.html": "collection",
    "browse.html": "browse",
    "listening-history.html": "history",
    "stations.html": "stations",
    "liked-songs.html": "liked",
    "playlist.html": "collection",
    "search.html": null,
    "admin.html": null,
    "login.html": null,
  };
  const key = map[path];
  document.querySelectorAll(".navLeft .navLink").forEach((a) => {
    const isActive = key && a.dataset.nav === key;
    a.classList.toggle("active", !!isActive);
  });
}

function wireGlobalSearch() {
  const input = document.getElementById("globalSearch");
  if (!input) return;

  // Bind once; in PJAX navigation the topbar persists, but the current page changes.
  if (input.__helixSearchBound) return;
  input.__helixSearchBound = true;

  // If we landed on search.html?q=..., reflect it in the top bar.
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    if (q && !input.value) input.value = q;
  } catch {}

  const isOnSearchPage = () => {
    const p = window.location.pathname || "";
    return p.endsWith("/search.html") || p.endsWith("search.html");
  };

  const go = () => {
    const value = (input.value || "").trim();
    if (!value) return;

    if (!isOnSearchPage()) {
      const url = `search.html?q=${encodeURIComponent(value)}`;
      // Prefer PJAX navigation if present so audio doesn't reset.
      if (typeof window.helixNavigate === "function") window.helixNavigate(url);
      else window.location.href = url;
      return;
    }
    // Let search page script decide what to do with it.
    window.dispatchEvent(new CustomEvent("helix:globalsearch", { detail: { q: value } }));
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      go();
    }
  });
}

function wireAuthUi(user) {
  const whoami = document.getElementById("whoami");
  if (whoami && user) whoami.textContent = `${user.username}`;

  const adminLink = document.getElementById("adminLink");
  if (adminLink && user?.role === "admin") adminLink.style.display = "inline-flex";

  const logoutLink = document.getElementById("logoutLink");
  if (logoutLink) {
    logoutLink.addEventListener("click", async (e) => {
      e.preventDefault();
      try { await backend.logout(); } catch {}
      window.location.href = "login.html";
    });
  }
}

export async function initTopNav() {
  setActiveNav();
  wireGlobalSearch();
  const user = await requireAuth({ redirectTo: "login.html" });
  if (user) wireAuthUi(user);
  return user;
}
