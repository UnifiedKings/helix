import * as backend from "./api/backend.js";
import { requireAdmin } from "./auth-guard.js";
import { startPlayerPolling } from "./player.js";

// NOTE: In PJAX mode we replace <main> during navigation, so any DOM references
// captured at module-load time become stale. Keep all DOM lookups inside init().

function $(id) {
  return document.getElementById(id);
}

function setText(el, t) {
  if (el) el.textContent = t || "";
}

function bindOnce(el, key, fn) {
  if (!el) return;
  const k = `__helixBound_${key}`;
  if (el[k]) return;
  el[k] = true;
  fn(el);
}

async function refreshSettings(dom) {
  const s = await backend.adminGetSettings();

  if (dom.subsonicBaseUrl) dom.subsonicBaseUrl.value = s.subsonic_base_url ?? "";
  if (dom.subsonicUsername) dom.subsonicUsername.value = s.subsonic_username ?? "";
  // For security, never auto-fill the password field.
  if (dom.subsonicPassword) dom.subsonicPassword.value = "";
  if (dom.firstPlayTimeout) dom.firstPlayTimeout.value = s.fulfillment_first_play_timeout_seconds ?? 10;
  if (dom.versionPreference) dom.versionPreference.value = s.fulfillment_version_preference ?? "prefer_studio";

  if (dom.searchDefaultCountry) dom.searchDefaultCountry.value = (s.search_default_country ?? "US");
  if (dom.searchHideNonOfficial) dom.searchHideNonOfficial.checked = !!(s.search_hide_non_official ?? true);
  if (dom.searchPreferOriginal) dom.searchPreferOriginal.checked = !!(s.search_prefer_original_release ?? false);
  if (dom.searchHideTracksWithoutArt) dom.searchHideTracksWithoutArt.checked = !!(s.search_hide_tracks_without_art ?? false);

  // Backend key is artist_images_enable_wikipedia (MusicBrainz -> Wikipedia thumbnail lookup).
  if (dom.artistImagesEnableWikidata) dom.artistImagesEnableWikidata.checked = !!(s.artist_images_enable_wikipedia ?? true);
  if (dom.imageProxyEnabled) dom.imageProxyEnabled.checked = !!(s.image_proxy_enabled ?? true);
  if (dom.imageCacheMaxMb) dom.imageCacheMaxMb.value = s.image_cache_max_mb ?? 500;
  if (dom.imageCacheThumbPx) dom.imageCacheThumbPx.value = s.image_cache_thumb_px ?? 256;
  if (dom.imageCacheTtlDays) dom.imageCacheTtlDays.value = s.image_cache_ttl_days ?? 90;
  if (dom.searchCacheTtlSeconds) dom.searchCacheTtlSeconds.value = s.search_cache_ttl_seconds ?? 300;
  if (dom.musicbrainzMinIntervalMs) dom.musicbrainzMinIntervalMs.value = s.musicbrainz_min_interval_ms ?? 1000;
  if (dom.musicbrainzUserAgent) dom.musicbrainzUserAgent.value = s.musicbrainz_user_agent ?? "";
}

async function saveSettings(dom) {
  setText(dom.settingsStatus, "Saving…");
  try {
    const patch = {
      subsonic_base_url: (dom.subsonicBaseUrl?.value || "").trim(),
      subsonic_username: (dom.subsonicUsername?.value || "").trim(),
      fulfillment_first_play_timeout_seconds: parseInt(dom.firstPlayTimeout?.value || "10", 10),
      fulfillment_version_preference: dom.versionPreference?.value || "prefer_studio",

      search_default_country: (dom.searchDefaultCountry?.value || "US").trim().toUpperCase(),
      search_hide_non_official: !!dom.searchHideNonOfficial?.checked,
      search_prefer_original_release: !!dom.searchPreferOriginal?.checked,
      search_hide_tracks_without_art: !!dom.searchHideTracksWithoutArt?.checked,

      // Backend key is artist_images_enable_wikipedia
      artist_images_enable_wikipedia: !!dom.artistImagesEnableWikidata?.checked,
      image_proxy_enabled: !!dom.imageProxyEnabled?.checked,
      image_cache_max_mb: parseInt(dom.imageCacheMaxMb?.value || "500", 10),
      image_cache_thumb_px: parseInt(dom.imageCacheThumbPx?.value || "256", 10),
      image_cache_ttl_days: parseInt(dom.imageCacheTtlDays?.value || "90", 10),
      search_cache_ttl_seconds: parseInt(dom.searchCacheTtlSeconds?.value || "300", 10),
      musicbrainz_min_interval_ms: parseInt(dom.musicbrainzMinIntervalMs?.value || "1000", 10),
      musicbrainz_user_agent: (dom.musicbrainzUserAgent?.value || "").trim(),
    };

    const pw = (dom.subsonicPassword?.value || "");
    if (pw.length > 0) patch.subsonic_password = pw;

    await backend.adminUpdateSettings(patch);
    setText(dom.settingsStatus, "Saved.");
  } catch (e) {
    setText(dom.settingsStatus, e?.message || String(e));
  }
}

async function refreshUsers(dom) {
  if (!dom.userTableWrap) return;
  const users = await backend.adminGetUsers();

  const rows = users
    .map((u) => {
      const activeChecked = u.is_active ? "checked" : "";
      return `<tr>
        <td style="padding:8px; border-bottom:1px solid #232833;">${u.username}</td>
        <td style="padding:8px; border-bottom:1px solid #232833;">
          <select data-user-role="${u.id}">
            <option value="user" ${u.role === "user" ? "selected" : ""}>user</option>
            <option value="admin" ${u.role === "admin" ? "selected" : ""}>admin</option>
          </select>
        </td>
        <td style="padding:8px; border-bottom:1px solid #232833;">
          <label class="muted">
            <input type="checkbox" data-user-active="${u.id}" ${activeChecked} />
            active
          </label>
        </td>
        <td style="padding:8px; border-bottom:1px solid #232833;">
          <button class="primary" data-user-save="${u.id}">Save</button>
        </td>
      </tr>`;
    })
    .join("");

  dom.userTableWrap.innerHTML = `<table style="width:100%; border-collapse:collapse;">
    <thead><tr>
      <th style="text-align:left; padding:8px; border-bottom:1px solid #232833;">Username</th>
      <th style="text-align:left; padding:8px; border-bottom:1px solid #232833;">Role</th>
      <th style="text-align:left; padding:8px; border-bottom:1px solid #232833;">Status</th>
      <th style="text-align:left; padding:8px; border-bottom:1px solid #232833;">Actions</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;

  dom.userTableWrap.querySelectorAll("button[data-user-save]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const id = e.target.getAttribute("data-user-save");
      const roleSel = dom.userTableWrap.querySelector(`select[data-user-role="${id}"]`);
      const activeChk = dom.userTableWrap.querySelector(`input[data-user-active="${id}"]`);

      const patch = {
        role: roleSel?.value || "user",
        is_active: !!activeChk?.checked,
      };

      setText(dom.createStatus, "Saving user…");
      try {
        await backend.adminUpdateUser(id, patch);
        setText(dom.createStatus, "User updated.");
        await refreshUsers(dom);
      } catch (err) {
        setText(dom.createStatus, err?.message || String(err));
      }
    });
  });
}

export async function init() {
  // Re-bind DOM on each navigation (PJAX replaces <main>). Topbar persists.
  startPlayerPolling();
  const dom = {
    whoami: $("whoami"),
    logoutLink: $("logoutLink"),

    // Settings
    subsonicBaseUrl: $("subsonicBaseUrl"),
    subsonicUsername: $("subsonicUsername"),
    subsonicPassword: $("subsonicPassword"),
    firstPlayTimeout: $("firstPlayTimeout"),
    versionPreference: $("versionPreference"),
    searchDefaultCountry: $("searchDefaultCountry"),
    searchHideNonOfficial: $("searchHideNonOfficial"),
    searchPreferOriginal: $("searchPreferOriginal"),
    searchHideTracksWithoutArt: $("searchHideTracksWithoutArt"),
    artistImagesEnableWikidata: $("artistImagesEnableWikidata"),
    imageProxyEnabled: $("imageProxyEnabled"),
    imageCacheMaxMb: $("imageCacheMaxMb"),
    imageCacheThumbPx: $("imageCacheThumbPx"),
    imageCacheTtlDays: $("imageCacheTtlDays"),
    searchCacheTtlSeconds: $("searchCacheTtlSeconds"),
    musicbrainzMinIntervalMs: $("musicbrainzMinIntervalMs"),
    musicbrainzUserAgent: $("musicbrainzUserAgent"),
    saveSettingsBtn: $("saveSettingsBtn"),
    settingsStatus: $("settingsStatus"),

    // Create user
    newUserName: $("newUserName"),
    newUserPass: $("newUserPass"),
    newUserRole: $("newUserRole"),
    createUserBtn: $("createUserBtn"),
    createStatus: $("createStatus"),

    // Users
    userTableWrap: $("userTableWrap"),
  };

  const user = await requireAdmin({ redirectTo: "index.html" });
  if (!user) return;
  if (dom.whoami) dom.whoami.textContent = `Signed in as ${user.username} (admin)`;

  // Ensure logout works even if topnav didn't wire it (admin has its own topbar on hard refresh).
  bindOnce(dom.logoutLink, "logout", (el) => {
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      try { await backend.logout(); } catch {}
      window.location.href = "login.html";
    });
  });

  bindOnce(dom.saveSettingsBtn, "saveSettings", (el) => {
    el.addEventListener("click", async () => {
      await saveSettings(dom);
    });
  });

  bindOnce(dom.createUserBtn, "createUser", (el) => {
    el.addEventListener("click", async () => {
      const u = (dom.newUserName?.value || "").trim();
      const p = dom.newUserPass?.value || "";
      const r = dom.newUserRole?.value || "user";

      if (!u || !p || p.length < 8) {
        setText(dom.createStatus, "Enter username and password (min 8 chars).");
        return;
      }

      setText(dom.createStatus, "Creating…");
      try {
        await backend.adminCreateUser(u, p, r);
        if (dom.newUserName) dom.newUserName.value = "";
        if (dom.newUserPass) dom.newUserPass.value = "";
        setText(dom.createStatus, "User created.");
        await refreshUsers(dom);
      } catch (e) {
        setText(dom.createStatus, e?.message || String(e));
      }
    });
  });

  try {
    await refreshSettings(dom);
    await refreshUsers(dom);
  } catch (e) {
    setText(dom.settingsStatus, e?.message || String(e));
  }
}
