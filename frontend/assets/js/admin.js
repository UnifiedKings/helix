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

  // Subsonic / Navidrome
  if (dom.subsonicBaseUrl) dom.subsonicBaseUrl.value = s.subsonic_base_url ?? "";
  if (dom.subsonicUsername) dom.subsonicUsername.value = s.subsonic_username ?? "";
  // For security, never auto-fill the password field.
  if (dom.subsonicPassword) dom.subsonicPassword.value = "";

  // Player
  if (dom.playerMaxQueueItems) dom.playerMaxQueueItems.value = s.player_max_queue_items ?? 50;

  // Search
  if (dom.searchDefaultCountry) dom.searchDefaultCountry.value = (s.search_default_country ?? "US");
  if (dom.searchHideNonOfficial) dom.searchHideNonOfficial.checked = !!(s.search_hide_non_official ?? true);
  if (dom.searchPreferOriginal) dom.searchPreferOriginal.checked = !!(s.search_prefer_original_release ?? true);
  if (dom.searchHideTracksWithoutArt) dom.searchHideTracksWithoutArt.checked = !!(s.search_hide_tracks_without_art ?? false);
  if (dom.searchCacheTtlSeconds) dom.searchCacheTtlSeconds.value = s.search_cache_ttl_seconds ?? 300;

  // MusicBrainz
  if (dom.musicbrainzMinIntervalMs) dom.musicbrainzMinIntervalMs.value = s.musicbrainz_min_interval_ms ?? 1000;
  if (dom.musicbrainzUserAgent) dom.musicbrainzUserAgent.value = s.musicbrainz_user_agent ?? "";
}


async function saveSettings(dom) {
  setText(dom.settingsStatus, "Saving…");
  try {
    const patch = {
      subsonic_base_url: (dom.subsonicBaseUrl?.value || "").trim(),
      subsonic_username: (dom.subsonicUsername?.value || "").trim(),
      // Only send password if provided (blank means "leave as-is").
      ...(dom.subsonicPassword?.value ? { subsonic_password: dom.subsonicPassword.value } : {}),

      player_max_queue_items: parseInt(dom.playerMaxQueueItems?.value || "50", 10),

      search_default_country: (dom.searchDefaultCountry?.value || "US").trim().toUpperCase(),
      search_hide_non_official: !!dom.searchHideNonOfficial?.checked,
      search_prefer_original_release: !!dom.searchPreferOriginal?.checked,
      search_hide_tracks_without_art: !!dom.searchHideTracksWithoutArt?.checked,
      search_cache_ttl_seconds: parseInt(dom.searchCacheTtlSeconds?.value || "300", 10),

      musicbrainz_min_interval_ms: parseInt(dom.musicbrainzMinIntervalMs?.value || "1000", 10),
      musicbrainz_user_agent: (dom.musicbrainzUserAgent?.value || "").trim(),
    };

    await backend.adminPatchSettings(patch);
    setText(dom.settingsStatus, "Saved.");
    await refreshSettings(dom);
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
    playerMaxQueueItems: $("playerMaxQueueItems"),
    subsonicBaseUrl: $("subsonicBaseUrl"),
    subsonicUsername: $("subsonicUsername"),
    subsonicPassword: $("subsonicPassword"),
    searchDefaultCountry: $("searchDefaultCountry"),
    searchHideNonOfficial: $("searchHideNonOfficial"),
    searchPreferOriginal: $("searchPreferOriginal"),
    searchHideTracksWithoutArt: $("searchHideTracksWithoutArt"),
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
